"""声の先読み(warm.py)と、エンジンの台詞の作り置き。"""
import os, sys, tempfile, threading, time, unittest
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))
os.environ.setdefault("SC_JOURNAL", os.path.join(tempfile.mkdtemp(), "j.json"))
import warm as W
import fun
import playfx
import quiz as q
import shiritori as sh


class FakeEng:
    """エンジンの作り置きまわりだけを真似た偽物。合成は呼ばれた回数を数える。"""
    def __init__(self, limit=1 << 30):
        self.cache, self.calls, self.limit, self.bytes = {}, 0, limit, 0
        self.last_voice = 0.0
    def line_key_for(self, text, voice, emotion=None, raw=None):
        return (text, voice, tuple(sorted((raw or emotion or {}).items())))
    def line_cached(self, text, voice, emotion=None, raw=None):
        return self.line_key_for(text, voice, emotion, raw) in self.cache
    def line_store(self, key, pcm):
        self.cache[key] = pcm
    def speech_pcm_as(self, text, voice, emotion=None, raw=None, pin=False):
        self.calls += 1
        pcm = np.ones(len(text) * 100, np.int16)
        if self.bytes + pcm.nbytes <= self.limit:
            self.cache[self.line_key_for(text, voice, emotion, raw)] = pcm
            self.bytes += pcm.nbytes
        return pcm


class ExpandTests(unittest.TestCase):
    def test_expand_tone_and_domains(self):
        self.assertEqual(W.expand("ずんだ", "いい しょうぶ{d}！"), ["いい しょうぶなのだ！"])
        self.assertEqual(len(W.expand("めたん", "{r}で あいこ！")), 3)                  # {r} は 3 つの手
        cs = W.expand("つむぎ", "せいかい！{cheer}")
        self.assertEqual(len(cs), len(playfx.CHEER["つむぎ"]))
        self.assertEqual(W.expand("ずんだ", "こたえは「{a}」だよ"), [])                    # 実行時にしか決まらない言葉は作らない
        self.assertEqual(W.expand("ずんだ", "{n}もん せいかい{d}！", cnt=[3, 4]), ["3もん せいかいなのだ！", "4もん せいかいなのだ！"])
        self.assertEqual(W.expand("つむぎ", "おみくじ、{n}かい ひいたね！", cnt=[3]), ["おみくじ、3かい ひいたね！"])

    def test_plan_covers_game_lines_without_leftover_tokens(self):
        plan = W.build_plan(["ずんだ", "めたん"])
        self.assertGreater(len(plan), 1000)
        prios = [p[0] for p in plan]
        self.assertEqual(prios, sorted(prios))                                          # 優先の順
        for _p, text, emo, raw in plan:
            self.assertNotRegex(text, r"\{\w+\}")
            self.assertTrue(emo or raw)
        texts = {p[1] for p in plan}
        for must in ("ガラガラ、ガラガラ…", "ホイ！"):
            self.assertIn(must, texts)
        self.assertTrue(any("ゆびを さす" in t for t in texts))                           # あっち向いてホイの、攻める側の台詞
        self.assertTrue(any(p[1].startswith("りんごなのだ！") for p in plan))             # しりとりのロボットの言葉(ずんだ)
        self.assertTrue(any(p[1].startswith("りんご、ですね。") for p in plan))           # めたん
        first = [p for p in plan if p[0] == 0]
        self.assertGreater(len(first), 50)                                              # 最初に言う文(あいさつ・案内)が先頭に集まる

    def test_game_lines_hit_the_plan(self):
        """遊びが実際に話す文は、先読みの計画にある(感情も同じ)。主な場面を抜き取って確かめる。"""
        plan = {(p[1], p[2], tuple(sorted(p[3].items())) if p[3] else None) for p in W.build_plan(["ずんだ"])}
        def has(text, emo=None, raw=None):
            return (text, emo, tuple(sorted(raw.items())) if raw else None) in plan
        self.assertTrue(has("ホイ！", raw=fun.RAW_HOI))
        self.assertTrue(has("あっちむいて〜…", raw=fun.RAW_AMUITE))
        self.assertTrue(has(sh.robot_word_line("ずんだ", "りんご", "ご"), "excited"))
        self.assertTrue(has("1もんめ！", "neutral"))
        self.assertTrue(has("ぼくもぐーなのだ！あいこなのだ！", "surprise"))        # じゃんけんのあいこ(ずんだ)
        self.assertTrue(has("だいきち！！おおよろこびなのだ！やったのだ！", None, fun.RAW_DAIKICHI))                                  # おみくじ 大吉
        self.assertTrue(has("ガラガラガラ…", None, fun.RAW_RATTLE))


class StepTests(unittest.TestCase):
    def mk(self, eng=None, busy=None, voice_ok=None):
        d = tempfile.mkdtemp()
        eng = eng or FakeEng()
        w = W.Warmer(lambda: eng, lambda: ["ずんだ"], busy=busy, voice_ok=voice_ok, dirpath=d)
        return w, eng, d

    def test_runs_in_priority_order_and_saves_to_disk(self):
        w, eng, d = self.mk()
        for _ in range(30):
            self.assertTrue(w.step())
        self.assertEqual(w.done, 30)
        self.assertEqual(eng.calls, 30)
        self.assertEqual(len([f for f in os.listdir(d) if f.endswith(".npy")]), 30)
        first = w.plan[:30]
        self.assertTrue(all(p[0] == 0 for p in first))

    def test_restart_loads_from_disk_without_synthesizing(self):
        w, eng, d = self.mk()
        for _ in range(20):
            w.step()
        eng2 = FakeEng()
        w2 = W.Warmer(lambda: eng2, lambda: ["ずんだ"], dirpath=d)
        for _ in range(20):
            self.assertTrue(w2.step())
        self.assertEqual(eng2.calls, 0)                                                  # 再起動のあとは、読むだけ
        self.assertEqual(len(eng2.cache), 20)

    def test_pauses_while_busy_and_resumes(self):
        busy = [True]
        w, eng, d = self.mk(busy=lambda: busy[0])
        self.assertFalse(w.step())
        self.assertEqual(w.state, "paused")
        self.assertEqual(eng.calls, 0)
        busy[0] = False
        self.assertTrue(w.step())
        self.assertEqual(eng.calls, 1)

    def test_waits_when_voice_server_is_down(self):
        up = [False]
        w, eng, d = self.mk(voice_ok=lambda: up[0])
        self.assertFalse(w.step())
        self.assertEqual(w.state, "waiting_voice")
        self.assertEqual(eng.calls, 0)
        up[0] = True
        self.assertTrue(w.step())

    def test_empty_synthesis_is_not_counted(self):
        class Dead(FakeEng):
            def speech_pcm_as(self, *a, **k):
                self.calls += 1
                return np.zeros(0, np.int16)
        w, eng, d = self.mk(Dead())
        self.assertFalse(w.step())
        self.assertEqual(w.done, 0)
        self.assertEqual(w.state, "waiting_voice")

    def test_stops_at_the_size_limit(self):
        w, eng, d = self.mk(FakeEng(limit=20000))
        n = 0
        while w.step() and n < 500:
            n += 1
        self.assertEqual(w.state, "full")
        self.assertLess(n, 500)
        self.assertFalse(w.step())
        self.assertEqual(w.snapshot()["state"], "full")

    def test_replan_when_personas_change(self):
        names = [["ずんだ"]]
        eng = FakeEng()
        w = W.Warmer(lambda: eng, lambda: names[0], dirpath=tempfile.mkdtemp())
        w.step()
        t1 = w.total()
        names[0] = ["ずんだ", "めたん"]
        w.step()
        self.assertGreater(w.total(), t1)

    def test_snapshot_progress(self):
        w, eng, d = self.mk()
        w.step()
        s = w.snapshot()
        self.assertEqual(s["done"], 1)
        self.assertGreater(s["total"], 1000)

    def test_thread_run_stops(self):
        w, eng, d = self.mk()
        w.stop_flag.set()
        t = threading.Thread(target=w.run)
        t.start()
        t.join(2.0)
        self.assertFalse(t.is_alive())


class EngineCacheTests(unittest.TestCase):
    def mk(self):
        import stackchan_bridge as sb
        e = sb.Engine.__new__(sb.Engine)
        e._line_cache = __import__("collections").OrderedDict()
        e._line_bytes = {True: 0, False: 0}
        e._line_lock = threading.Lock()
        e._voice_lock = threading.RLock()
        e.speed_scale, e.voice_ja, e.tts, e.prosody = 1.0, "voicevox:3", None, None
        e.calls = 0
        def synth(text, lang, src_dur=0.0, speed=None):
            e.calls += 1
            return np.ones(1600, np.int16)
        e._speech_pcm = synth
        return sb, e

    def test_same_line_is_synthesized_once(self):
        sb, e = self.mk()
        e.tts = type("T", (), {"voices": {}})()
        e._install_prosody = lambda: None
        a = e.speech_pcm_as("こんにちは", "voicevox:3", emotion={"speed": 1.1})
        b = e.speech_pcm_as("こんにちは", "voicevox:3", emotion={"speed": 1.1})
        self.assertEqual(e.calls, 1)
        e.speech_pcm_as("こんにちは", "voicevox:3", emotion={"speed": 1.2})              # 調子が違えば、別
        e.speech_pcm_as("さようなら", "voicevox:3", emotion={"speed": 1.1})
        self.assertEqual(e.calls, 3)
        e.speed_scale = 1.2                                                              # 話速の設定を変えたら、作り直す
        e.speech_pcm_as("こんにちは", "voicevox:3", emotion={"speed": 1.1})
        self.assertEqual(e.calls, 4)

    def test_unpinned_lines_are_evicted_pinned_stay(self):
        sb, e = self.mk()
        e.tts = type("T", (), {"voices": {}})()
        e._install_prosody = lambda: None
        e.LINE_FREE_BYTES = 3200 * 3
        e.speech_pcm_as("ピン", "voicevox:3", pin=True)
        for i in range(10):
            e.speech_pcm_as(f"文{i}", "voicevox:3")
        self.assertTrue(e.line_cached("ピン", "voicevox:3"))                              # 先読みの分は消えない
        self.assertLessEqual(e._line_bytes[False], 3200 * 3)
        self.assertFalse(e.line_cached("文0", "voicevox:3"))                              # その場の分は、古い順に捨てる

    def test_not_cached_when_speed_or_empty(self):
        sb, e = self.mk()
        e.tts = type("T", (), {"voices": {}})()
        e._install_prosody = lambda: None
        e.speech_pcm_as("あ", "voicevox:3", speed=1.3)
        e.speech_pcm_as("あ", "voicevox:3", src_dur=1.0)
        self.assertEqual(len(e._line_cache), 0)
        e._speech_pcm = lambda *a, **k: np.zeros(0, np.int16)
        e.speech_pcm_as("い", "voicevox:3")
        self.assertEqual(len(e._line_cache), 0)                                           # 合成できなかった(空)は残さない


class OmikujiCountTests(unittest.TestCase):
    def test_more_line_has_a_number(self):
        class G(q.Play):
            def __init__(s): s.kind = "omikuji"; s.robots = [{"name": "ずんだ"}]; s.pick = playfx.Picker(__import__("random").Random(1))
        g = G.__new__(G)
        g.kind, g.robots = "omikuji", [{"name": "ずんだ"}]
        g.pick = playfx.Picker(__import__("random").Random(1))
        for _ in range(10):
            t = g.L("more", fun.OMIKUJI["more"], cnt=3)
            self.assertIn("3かい", t)
            self.assertNotIn("のだかい", t)


if __name__ == "__main__":
    unittest.main()
