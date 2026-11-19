"""授業のシミュレーション: 6つの遊びを、子ども役の台本で最後まで動かし、機器に届いた指示(画面・声・顔)を時間つきで記録して、おかしな所を数える。
  PYTHONPATH=bridge python3 tests/sim_lessons.py [遊びの名前 ...]
"""
import os, sys, time, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_lesson import LessonBase
import playfx
playfx.Stage.FAST = True


class Sim(LessonBase):
    def setUp(self):
        super().setUp() if hasattr(super(), "setUp") else None

    def runTest(self):
        pass

    def start(self):
        les, gh, jh, qh, sent = self._lesson()
        self.log, self.t0 = [], time.time()
        said = self.said_ref = []
        for k, s in self.hub.ss.items():
            def mk(k, s):
                orig_send, orig_pcm = s.send, s._send_pcm
                def send(m):
                    self.log.append((time.time() - self.t0, "send", m)); return orig_send(m)
                def pcm(p, caption="", lang=""):
                    self.log.append((time.time() - self.t0, "say", caption)); return orig_pcm(p, caption, lang)
                s.send, s._send_pcm = send, pcm
            mk(k, s)
        return les, gh, jh, qh

    def dump(self, title, dev="a"):
        print(f"\n===== {title} =====")
        last = 0.0
        for t, kind, m in self.log:
            if kind == "say":
                print(f"{t:6.2f}  声   {m}")
            elif m.get("t") == "board" and not m.get("clear"):
                a, b = (m.get("ab") or m.get("ai") or m.get("aa") or ""), (m.get("bb") or m.get("bi") or m.get("ba") or "")
                print(f"{t:6.2f}  画面 [{m.get('st') or 'card'}] {m.get('title')} | {m.get('aw')}:{a}{('/'+m.get('am')) if m.get('am') else ''} | {m.get('bw')}:{b}{('/'+m.get('bm')) if m.get('bm') else ''} | {m.get('foot')}")
            elif m.get("t") == "board":
                print(f"{t:6.2f}  画面 クリア")
            elif m.get("t") == "face":
                print(f"{t:6.2f}  顔   {m.get('state')}")
            elif m.get("t") == "hush":
                print(f"{t:6.2f}  声を止める")

    def kid(self, les, hub_name, texts, dev="a", gap=0.0, timeout=8.0):
        """子どもの番になったら、台本の言葉を言う。"""
        h = getattr(self.hub, hub_name)
        for text in texts:
            g = h.games.get(dev)
            ok = self._wait(lambda: (h.games.get(dev) is not None and h.games[dev].turn == "kid") or not les.busy(dev), int(timeout * 20))
            if not les.busy(dev):
                return
            if not ok:
                print(f"  !! 子どもの番にならない(待ち {timeout}s) 台本={text}")
                return
            time.sleep(gap)
            h.say(text, dev)
            time.sleep(0.15)


def run(names):
    sim = Sim()
    out = {}
    plans = {
        "riddle": ("quiz", ["ちがうよ", "わかんない", "ぞう", "やめる"]),
        "shiritori": ("game", ["ごりら", "らっぱ", "ぱんだ", "だちょう", "うさぎ"]),
        "janken": ("janken", ["ぐー", "ぱー", "ちょき", "ぐー", "ぱー"]),
        "hoi": ("quiz", ["うえ", "ひだり", "みぎ", "した", "うえ", "ひだり"]),
        "omikuji": ("quiz", ["もういちど", "やめる"]),
        "mimic": ("quiz", ["おはよう", "ありがとう", "ぞうさん", "やめる"]),
    }
    for kind in names:
        les, gh, jh, qh = sim.start()
        hubn, script = plans[kind]
        les.start(kind, ["a"])
        sim.kid(les, hubn, script)
        time.sleep(0.5)
        if les.busy("a"):
            les.stop("a")
            time.sleep(0.3)
        sim.dump(kind)
        out[kind] = list(sim.log)
    return out


if __name__ == "__main__" and not os.environ.get("SIM"):
    kinds = sys.argv[1:] or ["riddle", "shiritori", "janken", "omikuji", "mimic"]
    run(kinds)


def silent(kinds, wait=40):
    """子どもが何も言わない: ちゃんと声をかけて、最後は自分で終わるか。"""
    sim = Sim()
    for kind in kinds:
        les, *_ = sim.start()
        les.start(kind, ["a"])
        t0 = time.time()
        while les.busy("a") and time.time() - t0 < wait:
            time.sleep(0.2)
        ended = not les.busy("a")
        if not ended:
            les.stop("a")
        sim.dump(f"{kind}(だれも何も言わない) 終わり方={'自分で終了' if ended else f'{wait}秒たっても終わらない'}")


def shiritori_chain():
    """しりとり: ロボットの言葉の最後の字から始まる言葉で、つなげる。"""
    import shiritori as sh
    sim = Sim()
    les, gh, jh, qh = sim.start()
    les.start("shiritori", ["a"])
    words = {}
    for w in sh.WORDS if isinstance(sh.WORDS, (list, tuple)) else []:
        words.setdefault(sh.first_char(w if isinstance(w, str) else w[0]), []).append(w if isinstance(w, str) else w[0])
    used = set()
    for _ in range(6):
        g = gh.games.get("a")
        if not sim._wait(lambda: (gh.games.get("a") is not None and gh.games["a"].turn == "kid") or not les.busy("a"), 200):
            print("  !! 子どもの番にならない"); break
        if not les.busy("a"): break
        need = gh.games["a"].need
        cand = [w for w in words.get(need, []) if w not in used and not w.endswith("ん")]
        if not cand:
            print(f"  (台本: 「{need}」から始まる言葉が辞書に無い)"); break
        w = cand[0]; used.add(w)
        gh.say(w, "a"); time.sleep(0.2)
    time.sleep(0.5)
    if les.busy("a"): les.stop("a")
    sim.dump("しりとり(つなげる)")


if __name__ == "__main__" and os.environ.get("SIM") == "silent":
    silent(["riddle", "shiritori", "janken", "hoi", "omikuji", "mimic"])
if __name__ == "__main__" and os.environ.get("SIM") == "chain":
    shiritori_chain()
