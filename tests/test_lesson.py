"""授業の進行役(lesson.py): 先生のボタンで開始・中止・切り替え。中止はすぐ効く。先生のボタンだけで始まる(子どもの声では始まらない)。"""
import os, sys, time, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_quiz import PlayBase
import kidchat as kc
import lesson as ls
import playfx

playfx.Stage.FAST = True


class LessonBase(PlayBase):
    def _lesson(self):
        gh, jh, qh, said, sent, raws = self._mk()
        hub = self.hub
        for k, s in hub.ss.items():
            s.dev_id = k
            s.alive = True
            s.muted_until = 1.0
        hub.live = lambda: list(hub.ss.values())
        hub.game, hub.janken, hub.quiz = gh, jh, qh
        hub.edu_on, hub.teacher_only, hub.kid_on, hub.kid_auto = True, True, True, True
        hub.menu_acts = list(kc.ACT_DEFAULT)
        hub.discussing = lambda: False
        hub.auto = None
        hub.listen = False
        les = ls.Lesson(hub)
        hub.lesson = les
        return les, gh, jh, qh, sent

    def hushes(self, sent, dev):
        return [m for n, m in sent if n == dev and m.get("t") == "hush"]


class LessonTests(LessonBase):
    def test_start_each_kind(self):
        les, gh, jh, qh, sent = self._lesson()
        for kind in ("shiritori", "janken", "riddle", "hoi", "omikuji", "mimic"):
            self.assertTrue(les.start(kind, ["a"])[0], kind)
            self.assertTrue(self._wait(lambda: les.current("a") == kind, 100), kind)
            les.stop("a")
            self.assertTrue(self._wait(lambda: not les.busy("a"), 200), kind)

    def test_unknown_kind_and_no_device(self):
        les, *_ = self._lesson()
        self.assertFalse(les.start("zzz", ["a"])[0])
        self.hub.live = lambda: []
        self.assertFalse(les.start("janken")[0])

    def test_switch_kind_stops_old_and_starts_new(self):
        les, gh, jh, qh, sent = self._lesson()
        self.assertTrue(les.start("riddle", ["a"])[0])
        self.assertTrue(self._wait(lambda: les.current("a") == "riddle", 100))
        t0 = time.time()
        self.assertTrue(les.start("janken", ["a"])[0])                       # 動いている遊びの上から、別の遊びを押した
        self.assertLess(time.time() - t0, 5.0)
        self.assertEqual(les.current("a"), "janken")
        self.assertFalse(qh.dev_busy("a"))
        self.assertTrue(self.hushes(sent, "a"))                              # 機器に「声を止めて」が届いた
        les.stop()

    def test_restart_same_kind(self):
        les, gh, jh, qh, sent = self._lesson()
        self.assertTrue(les.start("janken", ["a"])[0])
        self.assertTrue(les.start("janken", ["a"])[0])                       # 押しなおしても、止めて始め直す
        self.assertEqual(les.current("a"), "janken")
        les.stop()
        self.assertTrue(self._wait(lambda: not les.busy("a"), 200))

    def test_stop_all_is_immediate_and_hushes_every_device(self):
        les, gh, jh, qh, sent = self._lesson()
        self.assertTrue(les.start("janken")[0])                              # 全機器
        self.assertTrue(les.start("riddle", ["c"])[0])
        self.assertTrue(self._wait(lambda: all(les.busy(d) for d in "abc"), 100))
        les.stop()
        self.assertTrue(self._wait(lambda: not any(les.busy(d) for d in "abc"), 100))
        for d in "abc":
            self.assertTrue(self.hushes(sent, d), d)
            self.assertIn({"t": "board", "clear": True}, [m for n, m in sent if n == d])

    def test_stop_one_device_leaves_others(self):
        les, gh, jh, qh, sent = self._lesson()
        self.assertTrue(les.start("janken")[0])
        self.assertTrue(self._wait(lambda: all(les.busy(d) for d in "abc"), 100))
        les.stop("b")
        self.assertTrue(self._wait(lambda: not les.busy("b"), 100))
        self.assertTrue(les.busy("a") and les.busy("c"))
        self.assertFalse(self.hushes(sent, "a"))
        les.stop()

    def test_stop_when_idle_still_hushes(self):
        les, gh, jh, qh, sent = self._lesson()
        les.stop("a")                                                        # 何も遊んでいなくても、鳴りかけの声を止める
        self.assertTrue(self.hushes(sent, "a"))

    def test_snapshot_reports_each_device(self):
        les, gh, jh, qh, sent = self._lesson()
        self.assertTrue(les.start("hoi", ["b"])[0])
        self.assertTrue(self._wait(lambda: les.current("b") == "hoi", 100))
        devs = [{"id": i, "name": "N" + i, "online": True} for i in "abc"]
        snap = {x["id"]: x for x in les.snapshot(devs)}
        self.assertFalse(snap["a"]["on"])
        self.assertEqual(snap["a"]["label"], "")
        self.assertTrue(snap["b"]["on"])
        self.assertEqual((snap["b"]["kind"], snap["b"]["label"]), ("hoi", "あっちむいてホイ"))
        self.assertIn("msg", snap["b"]["detail"])
        les.stop()

    def test_snapshot_carries_board_picture(self):
        les, gh, jh, qh, sent = self._lesson()
        self.assertTrue(les.start("janken", ["a"])[0])
        self.assertTrue(self._wait(lambda: les.current("a") == "janken", 100))
        g = jh.games["a"]
        g._board(title="じゃんけん", a=("ずんだ", "ぐー"), b=("きみ", "ぱー"), foot="きみの かち！", ms=5000, a_icon="gu", b_icon="pa", fx="win")
        snap = {x["id"]: x for x in les.snapshot([{"id": "a", "name": "A", "online": True}])}
        b = snap["a"]["board"]
        self.assertEqual((b["ai"], b["bi"], b["fx"]), ("gu", "pa", "win"))
        les.stop("a")
        self.assertTrue(self._wait(lambda: not les.busy("a"), 200))
        self.assertIsNone({x["id"]: x for x in les.snapshot([{"id": "a", "name": "A", "online": True}])}["a"]["board"])   # 終わったら、絵は消える

    def test_stopped_game_does_not_speak_after_stop(self):
        les, gh, jh, qh, sent = self._lesson()
        self.assertTrue(les.start("omikuji", ["a"])[0])
        les.stop("a")
        self.assertTrue(self._wait(lambda: not les.busy("a"), 200))
        n = len([1 for n_, m in sent if n_ == "a"])
        time.sleep(0.5)
        self.assertEqual(len([1 for n_, m in sent if n_ == "a"]), n)         # 止まったあとは、何も送られない


class TeacherOnlyTests(LessonBase):
    def _kid(self):
        les, gh, jh, qh, sent = self._lesson()
        k = kc.KidChat(self.hub)
        self.hub.kid = k
        s = self.hub.ss["a"]
        s.eng = type("E", (), {"last_voice": 0.0})()
        return les, gh, jh, qh, sent, k, s

    def test_voice_cannot_start_a_game(self):
        les, gh, jh, qh, sent, k, s = self._kid()
        for t in ("あそぼう", "じゃんけんしよう", "しりとりしよう", "なぞなぞしよう", "おみくじひきたい", "きょうは、あめだったよ"):
            self.assertTrue(k.hear(s, t, "ja"), t)                           # 翻訳には回さない(True)が、何もしない
        time.sleep(0.3)
        self.assertFalse(any(les.busy(d) for d in "abc"))
        self.assertEqual([m for n, m in sent if m.get("t") in ("speech", "board")], [])

    def test_voice_cannot_switch_a_running_game(self):
        les, gh, jh, qh, sent, k, s = self._kid()
        self.assertTrue(les.start("shiritori", ["a"])[0])
        self.assertFalse(k.switch(s, "じゃんけんしよう"))
        self.assertEqual(les.current("a"), "shiritori")
        les.stop()

    def test_voice_can_start_when_teacher_only_is_off(self):
        les, gh, jh, qh, sent, k, s = self._kid()
        self.hub.teacher_only = False
        self.assertTrue(k.hear(s, "じゃんけんしよう", "ja"))
        self.assertTrue(self._wait(lambda: les.current("a") == "janken", 100))
        les.stop()

    def test_menu_board_hidden_when_teacher_only(self):
        import menuboard
        les, gh, jh, qh, sent, k, s = self._kid()
        self.assertEqual(menuboard.tick(self.hub), 0)
        self.hub.teacher_only = False
        self.assertGreater(menuboard.tick(self.hub), 0)


if __name__ == "__main__":
    unittest.main()


class ChunkTests(LessonBase):
    def test_split_chunks(self):
        import playfx
        self.assertEqual(playfx.split_chunks("ホイ！"), ["ホイ！"])
        self.assertEqual(playfx.split_chunks(""), [])
        long = "あっちむいてホイを しよう！ぼくが ゆびを さすから、おなじ むきを むかないでね。つぎは きみが ゆびを さす番。さきに 3てん とったら かち！"
        ch = playfx.split_chunks(long)
        self.assertGreaterEqual(len(ch), 3)
        self.assertEqual("".join(ch), long)                                 # 分けても、文字は変わらない
        self.assertTrue(all(len(c) >= playfx.CHUNK_PART_MIN for c in ch[:-1]))


    def test_first_sentence_is_sent_before_the_rest_is_made(self):
        import threading
        import shiritori as sh
        les, gh, jh, qh, sent = self._lesson()
        order = []
        gate = threading.Event()

        class Eng:
            face = None
            def speech_pcm_as(e, text, voice, emotion=None, raw=None):
                order.append(("synth", text))
                if "つぎは" in text:
                    gate.wait(3)                                            # 2 つめの文は、わざと遅くする
                import numpy as np
                return np.zeros(2400, dtype=np.float32)
        self.hub.ss["a"]._send_pcm = lambda pcm, caption="", lang="": (order.append(("send", caption)), 0.0)[1]
        g = gh.games.get("a") or sh.Shiritori(self.hub, None, gh.discuss, lambda: Eng(), gh.path, gh.rnd, stats=gh.stats, save_cb=gh.save)
        g.robots = [{"id": "a", "name": "ずんだ", "dev_name": "A", "voice": "voicevox:3"}]
        long = "あっちむいてホイを しよう！ぼくが ゆびを さすから、おなじ むきを むかないでね。つぎは きみが ゆびを さす番。さきに 3てん とったら かち！"
        t = threading.Thread(target=lambda: g._speak(Eng(), 0, long, "joy"), daemon=True)
        t.start()
        self.assertTrue(self._wait(lambda: any(o[0] == "send" for o in order), 60))      # 後ろの文を待たずに、先頭の文が送られた
        self.assertFalse(gate.is_set())
        gate.set()
        t.join(10)
        self.assertFalse(t.is_alive())
        sends = [o for o in order if o[0] == "send"]
        self.assertGreaterEqual(len(sends), 3)
        self.assertEqual(sends[0][1], long)                                  # 字幕は、最初の 1 回だけ全文
        self.assertTrue(all(s[1] == "" for s in sends[1:]))

    def test_stop_during_chunks_sends_no_more(self):
        import threading
        import shiritori as sh
        les, gh, jh, qh, sent = self._lesson()
        sends = []

        class Eng:
            face = None
            def speech_pcm_as(e, text, voice, emotion=None, raw=None):
                import numpy as np, time as _t
                _t.sleep(0.2)
                return np.zeros(2400, dtype=np.float32)
        self.hub.ss["a"]._send_pcm = lambda pcm, caption="", lang="": (sends.append(caption), 0.5)[1]
        g = sh.Shiritori(self.hub, None, gh.discuss, lambda: Eng(), gh.path, gh.rnd, stats=gh.stats, save_cb=gh.save)
        g.robots = [{"id": "a", "name": "ずんだ", "dev_name": "A", "voice": "voicevox:3"}]
        long = "あっちむいてホイを しよう！ぼくが ゆびを さすから、おなじ むきを むかないでね。つぎは きみが ゆびを さす番。さきに 3てん とったら かち！"
        t = threading.Thread(target=lambda: g._speak(Eng(), 0, long, "joy"), daemon=True)
        t.start()
        self.assertTrue(self._wait(lambda: len(sends) >= 1, 60))
        g._stop.set()
        t.join(5)
        self.assertFalse(t.is_alive())                                       # 中止したら、すぐ抜ける
        self.assertLess(len(sends), 4)

    def test_ack_on_button_press(self):
        les, gh, jh, qh, sent = self._lesson()
        t0 = time.time()
        self.assertTrue(les.start("omikuji", ["a"])[0])
        faces = [m for n, m in sent if n == "a" and m.get("t") == "face" and m.get("state") == "happy"]
        boards = [m for n, m in sent if n == "a" and m.get("t") == "board" and m.get("ab") == "はじまるよ！"]
        self.assertTrue(faces and boards)                                    # 押した瞬間の反応
        self.assertLess(time.time() - t0, 1.0)
        les.stop()


class RehearsalTests(LessonBase):
    """授業の通し稽古: 先生が管理画面で押す順番を、そのまま再現する。"""
    KINDS = ("shiritori", "janken", "riddle", "hoi", "omikuji", "mimic")

    def test_full_lesson_all_devices(self):
        les, gh, jh, qh, sent = self._lesson()
        ids = list(self.hub.ss)
        for kind in self.KINDS:                                  # 全機器で、6 つを順に(前の遊びの上から押す)
            self.assertTrue(les.start(kind, ids)[0], kind)
            for d in ids:
                self.assertTrue(self._wait(lambda: les.current(d) == kind, 200), (kind, d))
        les.stop()                                               # 全員の「中止」
        for d in ids:
            self.assertTrue(self._wait(lambda: not les.busy(d), 200), d)
        snap = les.snapshot([{"id": d, "name": d, "online": True} for d in ids])
        self.assertTrue(all(not s["on"] for s in snap))

    def test_rapid_button_mashing(self):
        les, gh, jh, qh, sent = self._lesson()
        for kind in self.KINDS * 2:                              # 連打しても、壊れず最後に押した遊びになる
            les.start(kind, ["a"])
        self.assertTrue(self._wait(lambda: les.current("a") == self.KINDS[-1], 300))
        les.stop("a")
        self.assertTrue(self._wait(lambda: not les.busy("a"), 200))

    def test_stop_then_next_immediately(self):
        les, gh, jh, qh, sent = self._lesson()
        for kind in self.KINDS:
            self.assertTrue(les.start(kind, ["a"])[0])
            les.stop("a")
            t0 = time.time()
            self.assertTrue(les.start("janken", ["a"])[0], kind)  # 中止の直後に、次の機能へ
            self.assertLess(time.time() - t0, 5.0)
            les.stop("a")

    def test_device_unplugged_mid_game(self):
        les, gh, jh, qh, sent = self._lesson()
        self.assertTrue(les.start("riddle", ["a"])[0])
        self.assertTrue(self._wait(lambda: les.current("a") == "riddle", 100))
        self.hub.ss["a"].alive = False                           # USB を抜いた
        les.stop("a")                                            # 先生が中止を押しても、例外にならない
        self.assertTrue(self._wait(lambda: not les.busy("a"), 300))
        snap = les.snapshot([{"id": "a", "name": "a", "online": False}])
        self.assertFalse(snap[0]["online"])
        self.assertFalse(snap[0]["on"])

    def test_snapshot_is_safe_while_games_change(self):
        les, gh, jh, qh, sent = self._lesson()
        ids = list(self.hub.ss)
        devs = [{"id": d, "name": d, "online": True} for d in ids]
        for kind in self.KINDS:
            les.start(kind, ids)
            for _ in range(5):
                les.snapshot(devs)
        les.stop()

    def test_nothing_speaks_until_button(self):
        les, gh, jh, qh, sent = self._lesson()
        time.sleep(0.3)
        self.assertFalse([m for n, m in sent if m.get("t") in ("audio", "say", "board")])
        self.assertFalse(les.busy("a"))
