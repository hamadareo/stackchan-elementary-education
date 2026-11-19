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
