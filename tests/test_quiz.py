import json
import os, random, sys, tempfile, time, unittest
os.environ["SC_JOURNAL"] = os.path.join(tempfile.mkdtemp(), "journal.json")
os.environ["SC_PERSONA"] = os.path.join(tempfile.mkdtemp(), "persona.json")
os.environ["SC_AUTONOMY"] = os.path.join(tempfile.mkdtemp(), "auto.json")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))
import quiz as q
import quizdata as qd
import fun
import playfx
import janken as jk
import shiritori as sh

POLITE = ("また", "ばいばい", "ありがとう", "たのしかった")           # やめるときの、気まずくない一言のどれか


def polite(said):
    return any(any(w in c for w in POLITE) for _n, c in said)


playfx.Stage.FAST = True                 # 演出の待ち時間を省く
fun.HOI_WINDOW, fun.HOI_HOLD = 1.2, 0.2     # 顔の向きを見る時間を短く


class FixedRandom(random.Random):
    """choice は先頭、random() は固定値(つられる確率の試験に使う)。"""
    fool = 0.0

    def choice(self, seq):
        return seq[0]

    def choices(self, population, weights=None, k=1):
        return [population[0]] * k

    def random(self):
        return self.fool


class JudgeTests(unittest.TestCase):
    def test_riddle_variants(self):
        it = q.Item("x", "フライパン", answers=["フライパン", "ふらいぱん"], hint="h", tries=2)
        for t in ("フライパン", "ふらいぱん", "フライパンだよ", "えっと、フライパン！", "ふらいぱんかな"):
            self.assertEqual(it.judge(t), "ok", t)
        self.assertEqual(it.judge("ほうちょう"), "ng")
        self.assertEqual(it.judge("ヒント"), "hint")
        self.assertEqual(it.judge("わからない"), "hint")
        self.assertEqual(it.judge(""), "unclear")

    def test_one_char_answer_is_exact(self):
        it = q.Item("x", "め", answers=["め", "目"], tries=2)
        self.assertEqual(it.judge("めだよ"), "ok")
        self.assertEqual(it.judge("目"), "ok")
        self.assertEqual(it.judge("あめ"), "ng")                     # 「あめ」の中の「め」には反応しない

    def test_riddle_data(self):
        self.assertGreaterEqual(len(qd.RIDDLES), 60)
        qs = [r[0] for r in qd.RIDDLES]
        self.assertEqual(len(qs), len(set(qs)))                      # 同じ問題が2つない
        for qu, ans, hint in qd.RIDDLES:
            self.assertTrue(qu and ans and hint, qu)
            self.assertTrue(all(isinstance(a, str) and a for a in ans))
            it = q.Item(qu, ans[0], answers=ans)
            self.assertEqual(it.judge(ans[0]), "ok", qu)             # 答えの先頭は、自分で正解になる


class LineTests(unittest.TestCase):
    def test_picker_avoids_repeats(self):
        p = playfx.Picker(random.Random(1))
        opts = ["a", "b", "c", "d"]
        last, same = None, 0
        for _ in range(200):
            o = p("k", opts)
            same += o == last
            last = o
        self.assertEqual(same, 0)                                    # 直前と同じものは出ない
        self.assertEqual(playfx.Picker(random.Random(1))("k", ["x"]), "x")
        two = playfx.Picker(random.Random(2))
        seq = [two("t", ["p", "q"]) for _ in range(20)]
        self.assertTrue(all(a != b for a, b in zip(seq, seq[1:])))   # 2つでも交互になる

    def test_tone(self):
        pk = lambda k, o: o[0]
        self.assertEqual(playfx.tone("ずんだ", "いい しょうぶ{d}！", pk), "いい しょうぶなのだ！")
        self.assertEqual(playfx.tone("めたん", "いい しょうぶ{d}！", pk), "いい しょうぶです！")
        self.assertEqual(playfx.tone("つむぎ", "いい しょうぶ{d}！", pk), "いい しょうぶだよ！")
        self.assertIn("やったのだ", playfx.tone("ずんだ", "{cheer}", pk))
        self.assertIn("くやしいです", playfx.tone("めたん", "{oops}", pk))

    def test_scene_banks_are_rich_and_tone_clean(self):
        for name in ("ずんだ", "めたん", "つむぎ"):
            for bank in (q.LINES, fun.HOI, fun.OMIKUJI, fun.MIMIC, playfx.COMMON, jk.JK):
                for key, opts in bank.items():
                    if key == "rank":
                        continue
                    for s in opts:
                        out = playfx.tone(name, s)
                        for tok in ("{d}", "{n}", "{cheer}", "{oops}"):
                            self.assertNotIn(tok, out)
        for key in ("ok", "retry", "reveal", "hint"):
            self.assertGreaterEqual(len(q.LINES[key]), 5, key)
        self.assertGreaterEqual(len(playfx.COMMON["bye"]), 8)


class DirTests(unittest.TestCase):
    def test_parse_dir(self):
        for t, d in [("うえ", "up"), ("上", "up"), ("あっちむいてホイ、うえ！", "up"), ("した", "down"), ("下むき", "down"), ("ひだり", "left"), ("左", "left"),
                     ("ヒダリ", "left"), ("みぎ", "right"), ("右だよ", "right"), ("みぎむきました", "right")]:
            self.assertEqual(fun.parse_dir(t), d, t)
        for t in ("ほい", "ホイ", "", "ひだりとみぎ", "ぐー"):
            self.assertIsNone(fun.parse_dir(t), t)

    def test_face_metrics_and_classify(self):
        row = [0, 0, 100, 100, 40, 40, 80, 40, 60, 70, 45, 90, 75, 90, 0.9]      # 右目(40,40) 左目(80,40) 鼻(60,70): 正面
        m = fun.face_metrics(row)
        self.assertAlmostEqual(m[0], 0.0, places=2)
        self.assertAlmostEqual(m[1], 0.75, places=2)
        self.assertIsNone(fun.face_metrics([0, 0, 1, 1, 10, 10, 12, 10, 11, 12]))        # 目が近すぎる(小さい・横向き)
        base = (0.0, 0.75)
        self.assertEqual(fun.classify_dir((0.02, 0.76), base), "center")
        self.assertEqual(fun.classify_dir((-0.4, 0.75), base), "right")       # 鼻が画像の左へ = 子どもの みぎ
        self.assertEqual(fun.classify_dir((0.4, 0.75), base), "left")
        self.assertEqual(fun.classify_dir((0.0, 0.45), base), "up")
        self.assertEqual(fun.classify_dir((0.0, 1.05), base), "down")
        self.assertEqual(fun.classify_dir((-0.1, 0.75), base), "center")      # 小さなずれは正面

    def test_vote_dir(self):
        self.assertEqual(fun.vote_dir(["center", "left", "left", "left"]), "left")
        self.assertEqual(fun.vote_dir(["left", "left", "center"]), "left")
        self.assertIsNone(fun.vote_dir(["center", "center", "left"]))           # 1 枚だけでは決めない
        self.assertIsNone(fun.vote_dir(["left", "right", "left", "right"]))     # 割れたら決めない
        self.assertIsNone(fun.vote_dir(["center"] * 4 + ["up", "up"]))         # 正面のほうが多い
        self.assertIsNone(fun.vote_dir([]))
        self.assertEqual(fun.baseline([]), (0.0, 0.85))
        self.assertEqual(fun.baseline([(0.0, 0.7), (0.2, 0.8), (0.1, 0.9)]), (0.1, 0.8))

    def test_omikuji_draw_data(self):
        rnd = random.Random(3)
        seen, last, runs = set(), "", 0
        for _ in range(400):
            d = fun.omikuji_draw(rnd, last, [], [])
            runs += d["yomi"] == last
            last = d["yomi"]
            seen.add((d["rank"], d["color"], d["item"], d["msg"]))
            self.assertNotIn("凶", d["rank"])
        self.assertEqual(runs, 0)                                    # 同じ運勢が 2 回続かない
        self.assertGreater(len(seen), 300)
        self.assertEqual({r[1] for r in fun.RANKS}, {"大吉", "中吉", "小吉", "吉", "末吉"})
        for k, ms in fun.OMIKUJI_MSG.items():
            self.assertGreaterEqual(len(ms), 7, k)
        self.assertGreaterEqual(len(fun.STYLES), 8)
        self.assertEqual(len({s[0] for s in fun.STYLES}), len(fun.STYLES))

    def test_kid_triggers(self):
        import kidchat as k
        for t, kind in [("なぞなぞしよう", "riddle"), ("あっちむいてホイしよう", "hoi"), ("あっち向いてほい", "hoi"), ("あっちむいてほいやりたい", "hoi"), ("おみくじ", "omikuji"),
                        ("おみくじひきたい", "omikuji"), ("ものまねしよう", "mimic"), ("まねっこ", "mimic"), ("おうむがえしやろう", "mimic")]:
            self.assertEqual(k.quiz_kind(t), kind, t)
        for t in ("これをえいごにして", "さんすうしよう", "クイズやろう", "かんじしよう", "えいごしよう"):          # 勉強の遊びは廃止
            self.assertIsNone(k.quiz_kind(t), t)
        self.assertEqual(k.ACT_DEFAULT, ["shiritori", "janken", "riddle", "hoi", "omikuji", "mimic"])
        for w in ("しりとり", "じゃんけん", "なぞなぞ", "あっちむいてホイ", "おみくじ", "ものまね"):
            self.assertIn(w, k.MENU)
            self.assertIn(w, k.menu_text(k.ACT_DEFAULT))
        self.assertNotIn("クイズ", k.MENU)


class PlayBase(unittest.TestCase):
    def _mk(self, seed=1, rnd=None):
        import discuss as d
        said, sent, raws = [], [], []

        class S:
            def __init__(se, n): se.n = n; se.hand_cb = None
            def send(se, m): sent.append((se.n, m))
            def _send_pcm(se, pcm, caption="", lang=""): said.append((se.n, caption)); return 0.0

        class Hub:
            def __init__(h): h.ss = {i: S(i) for i in "abc"}; h.events = []
            def by_id(h, i): return h.ss.get(i)
            def event(h, t): h.events.append(t)

        class Reg:
            def all(h): return [{"id": i, "name": "N" + i} for i in "abc"]

        class Eng:
            face = None
            def translate(e, m): return "?", "x"
            def speech_pcm_as(e, text, voice, emotion=None, raw=None): raws.append((text, raw)); return b""

        hub = Hub()
        ds = d.Discussion(hub, Reg(), lambda: Eng(), d.Memory(os.path.join(tempfile.mkdtemp(), "m.json")), d.Journal(os.path.join(tempfile.mkdtemp(), "j.json")))
        path = os.path.join(tempfile.mkdtemp(), "g.json")
        args = (hub, Reg(), ds, lambda: Eng(), path)
        r = rnd or random.Random(seed)
        gh, jh, qh = sh.GameHub(*args, r), jk.JankenHub(*args, r), q.QuizHub(*args, r)
        hubs = [gh, jh, qh]
        for h in hubs:
            h.peers = [x for x in hubs if x is not h]
        self.hub = hub
        return gh, jh, qh, said, sent, raws

    def _wait(self, cond, n=400):
        for _ in range(n):
            if cond(): return True
            time.sleep(0.05)
        return False

    def ans(self, qh, g, text, dev="a"):
        """答えを入れて、遊びがそれを受け取る(子どもの番が終わる)まで待つ。"""
        self.assertTrue(self._wait(lambda: g.turn == "kid" or not g.on))
        self.assertTrue(qh.say(text, dev))
        self.assertTrue(self._wait(lambda: g.turn != "kid" or not g.on))

    def _turn(self, g):
        self.assertTrue(self._wait(lambda: g.turn == "kid" or not g.on))
        self.assertTrue(g.on)

    def step(self, qh, g, phase, text, dev="a"):
        """あっち向いてホイの場面 phase で、子どもの番になるまで待って、text を入れる。"""
        self.assertTrue(self._wait(lambda: (g.phase == phase and g.turn == "kid") or not g.on), phase)
        self.assertTrue(g.on, phase)
        self.assertTrue(qh.say(text, dev))
        self.assertTrue(self._wait(lambda: g.turn != "kid" or not g.on))

    def servo_sum(self, sent, dev="a"):
        dp = dt = 0
        for n, m in sent:
            if n == dev and m.get("t") == "servo":
                dp += m.get("dpan", 0)
                dt += m.get("dtilt", 0)
        return dp, dt


class RiddleTests(PlayBase):
    def test_riddle_round_hint_reveal_and_end(self):
        gh, jh, qh, said, sent, _r = self._mk()
        self.assertTrue(qh.start("riddle", "", ["a"])[0])
        g = qh.games["a"]
        self._turn(g)
        self.assertEqual(g.qno, 1)
        self.assertTrue(any("1もんめ" in c for _n, c in said))              # ロボットから問題を出す
        self.ans(qh, g, g.cur.answers[0])                                      # せいかい
        self.assertTrue(self._wait(lambda: g.score == 1))
        self._turn(g)
        self.assertEqual(g.qno, 2)
        self.ans(qh, g, "ヒント")                                                 # 1 回目: ヒント
        self.assertTrue(self._wait(lambda: any("ヒント" in c for _n, c in said)))
        self._turn(g)
        self.ans(qh, g, "わからない")                                             # 2 回目: 答えを教える
        self.assertTrue(self._wait(lambda: any("こたえ" in c for _n, c in said)))
        self.assertEqual(g.score, 1)
        for n in range(3, 6):                                                # 残りは全部せいかい
            self._turn(g)
            self.assertEqual(g.qno, n)
            self.ans(qh, g, g.cur.answers[0])
        self.assertTrue(self._wait(lambda: g.stat.startswith("せいかい 4")))
        self.assertEqual(g.score, 4)
        self._turn(g)
        self.ans(qh, g, "いいえ")                                                 # もういっかい? → いいえ
        self.assertTrue(self._wait(lambda: not g.on))
        d = json.load(open(qh.path))["quiz"]
        self.assertEqual((d["plays"], d["rounds"], d["correct"], d["answered"]), (1, 1, 4, 5))
        self.assertTrue(all(who == "a" for who, _c in said))                  # 機器 a だけが話す(ほかの機器は連携しない)
        self.assertEqual(self.servo_sum(sent), (0, 0))                        # 首の演出のあと、正面に戻っている

    def test_riddle_streak_and_wrong_then_reveal_board(self):
        gh, jh, qh, said, sent, _r = self._mk(5)
        qh.start("riddle", "", ["b"])
        g = qh.games["b"]
        self._turn(g)
        self.ans(qh, g, g.cur.answers[0], "b")
        self._turn(g)
        self.ans(qh, g, g.cur.answers[0], "b")
        self.assertTrue(self._wait(lambda: any("れんぞく" in c or "つづけて" in c for _n, c in said)))
        self._turn(g)
        self.ans(qh, g, "ぜんぜんちがう", "b")                                     # ちがう → もう一度
        self._turn(g)
        self.ans(qh, g, "まだちがう", "b")                                        # 2 回目 → 答えを教える
        self.assertTrue(self._wait(lambda: any("こたえ" in c for _n, c in said)))
        self.assertTrue(any(m.get("t") == "servo" for n, m in sent))           # 首の演出(うなずく・かしげる)が送られた
        qh.stop("b")
        self.assertTrue(self._wait(lambda: not g.on))
        self.assertEqual(self.servo_sum(sent, "b"), (0, 0))

    def test_play_again_and_quit(self):
        gh, jh, qh, said, sent, _r = self._mk(2)
        qh.start("riddle", "", ["b"])
        g = qh.games["b"]
        for n in range(1, 6):
            self._turn(g)
            self.ans(qh, g, g.cur.answers[0], "b")
        self.assertTrue(self._wait(lambda: g.stat.startswith("せいかい 5")))
        self._turn(g)
        self.ans(qh, g, "やる", "b")                                              # もういっかい → 2 ラウンド目
        self.assertTrue(self._wait(lambda: g.qno == 1 and g.on and g.turn == "kid"))
        self.ans(qh, g, "やめよう", "b")                                           # 途中でやめる
        self.assertTrue(self._wait(lambda: not g.on))
        self.assertTrue(polite(said))                                          # やめても気まずくない一言
        d = json.load(open(qh.path))["quiz"]
        self.assertEqual((d["rounds"], d["perfect"]), (1, 1))

    def test_idle_ends_politely(self):
        gh, jh, qh, said, sent, _r = self._mk()
        old = q.IDLE_WAIT
        q.IDLE_WAIT = 0.15
        try:
            qh.start("riddle", "", ["a"])
            g = qh.games["a"]
            self.assertTrue(self._wait(lambda: not g.on, 600))
        finally:
            q.IDLE_WAIT = old
        self.assertTrue(polite(said))


class ExclusionTests(PlayBase):
    def test_one_play_per_device_and_independent(self):
        gh, jh, qh, said, sent, _r = self._mk()
        self.assertTrue(qh.start("riddle", "", ["a"])[0])
        ok, err = qh.start("omikuji", "", ["a"])
        self.assertFalse(ok)                                          # 同じ機器で 2 つはできない
        ok, err = gh.start("robot", "easy", ["a"])
        self.assertFalse(ok)
        self.assertIn("あそび", err)
        ok, err = jh.start("robot", "easy", ["a"])
        self.assertFalse(ok)
        self.assertTrue(qh.start("omikuji", "", ["b"])[0])            # 別の機器は、別の遊びを独立にできる
        self.assertTrue(qh.start("mimic", "", ["c"])[0])
        self.assertTrue(self._wait(lambda: qh.games["b"].on and qh.games["c"].on))
        self.assertEqual({type(g).__name__ for g in qh.games.values()}, {"Quiz", "Omikuji", "Mimic"})
        self.assertFalse(qh.start("nazo", "", ["a"])[0])             # 知らない遊びは始まらない
        qh.stop()
        self.assertTrue(self._wait(lambda: not qh.own_on))
        self.assertTrue(qh.start("hoi", "", ["a"])[0])               # 終わった機器は、別の遊びに切り替えられる
        qh.stop()
        self.assertTrue(self._wait(lambda: not qh.own_on))
        for d in "abc":
            self.assertEqual(self.servo_sum(sent, d), (0, 0))

    def test_hear_routes_only_to_playing_device(self):
        gh, jh, qh, said, sent, _r = self._mk()
        qh.start("mimic", "", ["a"])
        self._wait(lambda: qh.games["a"].turn == "kid")
        self.assertFalse(qh.hear("ぞう", "ja", "b"))                   # 遊んでいない機器の声は、遊びに流さない
        self.assertTrue(qh.hear("ぞう", "en", "a"))                     # 遊び中は、翻訳に回さない
        qh.stop()
        self._wait(lambda: not qh.own_on)


class HoiTests(PlayBase):
    def _start(self, fool=0.0):
        rnd = FixedRandom(1)
        rnd.fool = fool
        gh, jh, qh, said, sent, raws = self._mk(rnd=rnd)
        self.assertTrue(qh.start("hoi", "", ["a"])[0])
        return qh, qh.games["a"], said, sent, raws

    def test_kid_wins_round_with_pointing_and_fooled_robot(self):
        qh, g, said, sent, raws = self._start(fool=0.0)                # つられる(同じ向きを向く)
        for i, hand in enumerate(("ぱー", "ぱー", "ぐー")):               # ロボットの手は ぐー・ぐー・ちょき(同じ手が 3 回続かない) → 3 回とも子どもの かち
            self.step(qh, g, "jan", hand)
            self.step(qh, g, "ask_dir", "ひだりをさした")
            self.assertTrue(self._wait(lambda: g.kp >= i + 1))
        self.assertTrue(self._wait(lambda: g.stat.startswith("きみ 3てん") and g.turn == "kid"))
        self.assertEqual((g.kp, g.rp), (3, 0))
        self.assertTrue(qh.say("いいえ", "a"))
        self.assertTrue(self._wait(lambda: not g.on))
        d = json.load(open(qh.path))["quiz"]
        self.assertEqual((d["hoi_rounds"], d["hoi_wins"], d["hoi_points"], d["hoi_streak_best"]), (1, 1, 3, 1))
        self.assertEqual(self.servo_sum(sent), (0, 0))                 # 首を向けたあと、正面に戻っている
        pans = [m["dpan"] for n, m in sent if n == "a" and m.get("t") == "servo" and m.get("dpan")]
        self.assertTrue(any(p < 0 for p in pans))                      # 子どもの ひだり = ロボットの パン(-)
        bs = [m for n, m in sent if m.get("t") == "board"]
        self.assertTrue(any(m.get("title") == "じゃんけん" and m.get("bb") == "ぱー" and m.get("ab") == "ぐー" for m in bs))     # 手の表示
        self.assertTrue(any(m.get("ba") == "left" and m.get("aa") == "left" for m in bs))                                  # 指さしの向き(三角)
        self.assertTrue(any(m.get("title") == "けっか" for m in bs))
        self.assertTrue(any(m.get("clear") for m in bs))               # 終わるとき、表示を消す

    def test_robot_dodges_when_not_fooled(self):
        qh, g, said, sent, raws = self._start(fool=0.99)
        self.step(qh, g, "jan", "ぱー")
        self.step(qh, g, "ask_dir", "うえ")
        self.assertTrue(self._wait(lambda: g.attacks == 1))
        self.assertEqual((g.kp, g.rp), (0, 0))                        # よけられた(同じ向きを向かない)
        self.assertTrue(any(h["q"].startswith("きみが うえ") and not h["ok"] for h in g.history))
        qh.stop("a")
        self.assertTrue(self._wait(lambda: not g.on))

    def test_robot_wins_point_by_voice_and_dodge(self):
        qh, g, said, sent, raws = self._start()
        self.step(qh, g, "jan", "ちょき")                                # ロボット ぐー の かち。ロボットは うえ(先頭) を向く
        self.step(qh, g, "watch", "うえ")                                 # 子どもが うえ を向いた → ロボットの 1 点
        self.assertTrue(self._wait(lambda: g.rp == 1))
        self.step(qh, g, "jan", "ちょき")
        self.step(qh, g, "watch", "みぎ")                                 # よけた → 点にならない
        self.assertTrue(self._wait(lambda: g.attacks == 2))
        self.assertEqual((g.kp, g.rp), (0, 1))
        self.assertTrue(any(raw is not None and raw.get("speed", 0) > 1.0 for t, raw in raws if "ホイ" in t))   # 「ホイ」は盛り上げた声
        qh.stop("a")
        self.assertTrue(self._wait(lambda: not g.on))
        self.assertEqual(self.servo_sum(sent), (0, 0))
        snap = g.snapshot()
        self.assertEqual(snap["robot_points"], 1)
        self.assertIn("ロボット 1てん", snap["stat"])

    def test_draw_repeats_and_quit(self):
        qh, g, said, sent, raws = self._start()
        self.step(qh, g, "jan", "ぐー")                                  # ロボット ぐー: あいこ
        self.assertTrue(self._wait(lambda: any("あいこ" in c for _n, c in said)))
        self.step(qh, g, "jan", "やめよう")                               # 途中でやめる
        self.assertTrue(self._wait(lambda: not g.on))
        self.assertTrue(polite(said))
        self.assertEqual(g.kp + g.rp, 0)

    def test_unseen_direction_is_not_a_point(self):
        qh, g, said, sent, raws = self._start()
        self.step(qh, g, "jan", "ちょき")
        # 声も顔も見えない(カメラなし・声なし): 判定できず、もういっかい(点にならない)
        self.assertTrue(self._wait(lambda: any("みえなかった" in c or "わからなかった" in c for _n, c in said), 600))
        self.assertEqual((g.kp, g.rp), (0, 0))
        qh.stop("a")
        self.assertTrue(self._wait(lambda: not g.on))
        self.assertEqual(self.servo_sum(sent), (0, 0))


class OmikujiTests(PlayBase):
    def test_draw_again_and_variety(self):
        rnd = FixedRandom(1)
        gh, jh, qh, said, sent, raws = self._mk(rnd=rnd)
        self.assertTrue(qh.start("omikuji", "", ["a"])[0])
        g = qh.games["a"]
        self._turn(g)                                                  # 1 回目を引いて、「もういっかい?」
        self.assertEqual(g.draws, 1)
        self.assertTrue(any("ガラガラ" in c for _n, c in said))
        self.assertTrue(any("ラッキーカラー" in c for _n, c in said))
        first = g.last_rank
        self.ans(qh, g, "やる")
        self._turn(g)
        self.assertEqual(g.draws, 2)
        self.assertNotEqual(g.last_rank, first)                       # 同じ運勢が続かない
        bs = [m for n, m in sent if m.get("t") == "board" and m.get("title") == "おみくじ" and m.get("ab") in ("大吉", "中吉", "小吉", "吉", "末吉")]
        self.assertGreaterEqual(len(bs), 2)
        self.assertTrue(all("/" in m["foot"] for m in bs))             # ラッキーカラーとラッキーなもの
        self.assertTrue(any(m.get("dpan") for n, m in sent if m.get("t") == "servo"))        # 「ガラガラ」で首をふる
        self.ans(qh, g, "いいえ")
        self.assertTrue(self._wait(lambda: not g.on))
        self.assertEqual(json.load(open(qh.path))["quiz"]["omikuji_n"], 2)
        self.assertEqual(self.servo_sum(sent), (0, 0))
        self.assertTrue(polite(said) or any("ひこう" in c or "よんでね" in c or "いちにち" in c for _n, c in said))
        self.assertTrue(g.stat.startswith("ひいた かず 2"))

    def test_many_draws_never_crash(self):
        gh, jh, qh, said, sent, raws = self._mk(7)
        qh.start("omikuji", "", ["a"])
        g = qh.games["a"]
        for i in range(8):
            self._turn(g)
            self.ans(qh, g, "もういっかい")
        self.assertTrue(self._wait(lambda: g.draws == 9 and g.turn == "kid"))
        qh.stop("a")
        self.assertTrue(self._wait(lambda: not g.on))
        self.assertEqual(self.servo_sum(sent), (0, 0))
        self.assertTrue(all("凶" not in c for _n, c in said))


class MimicTests(PlayBase):
    def test_echo_styles_again_and_filters(self):
        gh, jh, qh, said, sent, raws = self._mk()
        self.assertTrue(qh.start("mimic", "", ["a"])[0])
        g = qh.games["a"]
        self.ans(qh, g, "ぞう")
        self.assertTrue(self._wait(lambda: any(t == "ぞう" and r for t, r in raws)))
        first = [r for t, r in raws if t == "ぞう"][-1]
        self.assertTrue({"speed", "pitch", "intonation", "volume"} <= set(first))
        self.ans(qh, g, "もういっかい")                                  # 同じ言葉を、べつの声で
        self.assertTrue(self._wait(lambda: len([1 for t, r in raws if t == "ぞう"]) == 2))
        second = [r for t, r in raws if t == "ぞう"][-1]
        self.assertNotEqual(first, second)
        n = len(raws)
        self.ans(qh, g, "ご視聴ありがとうございました")                     # 雑音の決まり文句はまねしない
        self.ans(qh, g, "しねしね")                                      # 子どもに まねさせない言葉は、まねせず ことば遊びに戻す
        self.assertTrue(self._wait(lambda: any("ほかの" in c or "ことばあそび" in c for _n, c in said)))
        self.assertFalse(any(t in ("しねしね", "ご視聴ありがとうございました") for t, r in raws[n:]))
        self.assertEqual(g.words, 1)
        bs = [m for n_, m in sent if m.get("t") == "board" and m.get("title") == "ものまね"]
        self.assertTrue(bs and bs[0].get("ab") == "ぞう" and bs[0].get("bw"))       # 言った言葉と、声の名前
        self.ans(qh, g, "やめよう")
        self.assertTrue(self._wait(lambda: not g.on))
        self.assertEqual(self.servo_sum(sent), (0, 0))

    def test_round_ends_after_five_words(self):
        gh, jh, qh, said, sent, raws = self._mk(3)
        qh.start("mimic", "", ["b"])
        g = qh.games["b"]
        for w in ("いぬ", "ねこ", "りんご", "ロボット", "ありがとう"):
            self.ans(qh, g, w, "b")
        self.assertTrue(self._wait(lambda: not g.on))
        d = json.load(open(qh.path))["quiz"]
        self.assertEqual((d["mimic_n"], d["mimic_rounds"]), (5, 1))
        used = [r for t, r in raws if t in ("いぬ", "ねこ", "りんご", "ロボット", "ありがとう")]
        self.assertEqual(len(used), 5)
        self.assertEqual(len({json.dumps(r, sort_keys=True) for r in used}), 5)    # 5 回とも、ちがう声
        self.assertTrue(any("5つ" in c or "いっぱい" in c for _n, c in said))

    def test_idle_ends_politely(self):
        gh, jh, qh, said, sent, raws = self._mk()
        old = fun.IDLE_WAIT
        fun.IDLE_WAIT = 0.15
        try:
            qh.start("mimic", "", ["a"])
            g = qh.games["a"]
            self.assertTrue(self._wait(lambda: not g.on, 600))
        finally:
            fun.IDLE_WAIT = old
        self.assertTrue(polite(said))


class SnapshotTests(PlayBase):
    def test_snapshot_fields_for_admin(self):
        gh, jh, qh, said, sent, raws = self._mk()
        qh.start("omikuji", "", ["a"])
        g = qh.games["a"]
        self._turn(g)
        s = qh.snapshot()
        self.assertTrue(s["on"])
        x = s["games"][0]
        for key in ("dev_id", "kind", "kind_label", "turn", "msg", "question", "answer", "stat", "big", "history", "score", "qno", "rounds"):
            self.assertIn(key, x)
        self.assertEqual(x["kind_label"], "おみくじ")
        self.assertEqual(set(s["kinds"]), {"riddle", "hoi", "omikuji", "mimic"})
        for key in ("hoi_wins", "omikuji_n", "mimic_n", "plays"):
            self.assertIn(key, s)
        qh.stop()
        self._wait(lambda: not qh.own_on)


class JankenShiritoriStageTests(PlayBase):
    def test_janken_shows_board_and_cleans_up(self):
        gh, jh, qh, said, sent, raws = self._mk(rnd=FixedRandom(1))
        self.assertTrue(jh.start("robot", "easy", ["a"])[0])
        g = jh.games["a"]
        self.assertTrue(self._wait(lambda: g.turn == "kid"))
        self.assertTrue(jh.say("ぱー", "a"))                             # ロボット ぐー → 子どもの かち
        self.assertTrue(self._wait(lambda: g.wins == 1))
        self.assertTrue(self._wait(lambda: any(m.get("title") == "じゃんけん" and m.get("bb") == "ぱー" and m.get("ab") == "ぐー" and "きみ" in m.get("foot", "") for n, m in sent if m.get("t") == "board")))
        jh.stop("a")
        self.assertTrue(self._wait(lambda: not g.on))
        self.assertTrue(any(m.get("clear") for n, m in sent if m.get("t") == "board"))

    def test_shiritori_board(self):
        gh, jh, qh, said, sent, raws = self._mk(rnd=random.Random(4))
        self.assertTrue(gh.start("robot", "easy", ["a"])[0])
        g = gh.games["a"]
        self.assertTrue(self._wait(lambda: g.turn == "kid" and g.chain))
        word = g.chain[0]["word"]
        self.assertTrue(any(m.get("title") == "しりとり" and m.get("aw") and m.get("ab") == word for n, m in sent if m.get("t") == "board"))
        gh.stop("a")
        self.assertTrue(self._wait(lambda: not g.on))


if __name__ == "__main__":
    unittest.main()
