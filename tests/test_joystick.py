import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))
import joystick as js


class PadTests(unittest.TestCase):
    def mk(self):
        self.n = 0
        p = js.Pad(lambda: setattr(self, "n", self.n + 1))
        for _ in range(15):
            p.feed(510, 515, 0)                                  # 中心合わせ
        return p

    def test_parse(self):
        self.assertEqual(js.parse("J,512,498,0\r\n"), (512, 498, 0))
        self.assertEqual(js.parse("J,1,2,1"), (1, 2, 1))
        for bad in ("", "J,1,2", "X,1,2,3", "J,a,2,0", "J,2000,2,0"):
            self.assertIsNone(js.parse(bad), bad)

    def test_push_any_direction_jumps_once_until_released(self):
        for x, y in ((1000, 515), (10, 515), (510, 1000), (510, 10)):
            p = self.mk(); self.n = 0
            p.feed(x, y, 0); p.feed(x, y, 0); p.feed(x, y, 0)
            self.assertEqual(self.n, 1, (x, y))                  # 倒しっぱなしでも 1 回
            p.feed(510, 515, 0); p.feed(x, y, 0)
            self.assertEqual(self.n, 2, (x, y))                  # 戻して、また倒すと 2 回

    def test_small_wobble_is_ignored(self):
        p = self.mk()
        for x in (480, 540, 530, 500):
            p.feed(x, 515, 0)
        self.assertEqual(self.n, 0)

    def test_button_press_jumps_once(self):
        p = self.mk()
        p.feed(510, 515, 1); p.feed(510, 515, 1); p.feed(510, 515, 0); p.feed(510, 515, 1)
        self.assertEqual(self.n, 2)

    def test_no_jump_during_calibration(self):
        n = []
        p = js.Pad(lambda: n.append(1))
        for _ in range(10):
            p.feed(1000, 1000, 1)
        self.assertEqual(n, [])

    def test_jump_goes_only_to_chari_players(self):
        sent = []

        class G:
            def __init__(self, on, kind): self.on, self.kind = on, kind
            def _dev(self, cmd): sent.append(cmd)

        class Q: games = {"a": G(True, "chari"), "b": G(True, "riddle"), "c": G(False, "chari")}

        class H: quiz = Q()
        self.assertEqual(js.Joystick(H()).jump(), 1)
        self.assertEqual(sent, ["jump"])


if __name__ == "__main__":
    unittest.main()
