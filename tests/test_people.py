import os, sys, tempfile, unittest
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))
import people


def frame(hand_x=None, noise=False, rng=None):
    g = np.full((60, 80), 90, np.uint8)
    g[10:30, 30:50] = 160                      # 顔 (x30-50,y10-30)
    if hand_x is not None:
        g[8:20, hand_x:hand_x + 8] = 230        # 手
    if noise:
        g = np.clip(g.astype(int) + rng.integers(-3, 4, g.shape), 0, 255).astype(np.uint8)
    return g


FACE = (30, 10, 50, 30)


class WaveTest(unittest.TestCase):
    def run_seq(self, xs, dt=0.35, noise=False):
        w, rng, t, fired = people.WaveDetector(), np.random.default_rng(1), 100.0, False
        for x in xs:
            fired |= w.update(frame(x, noise, rng), FACE, t)
            t += dt
        return fired

    def test_wave(self):
        self.assertTrue(self.run_seq([55, 62, 55, 64, 56, 63, 55, 62, 56, 63]))

    def test_still(self):
        self.assertFalse(self.run_seq([None] * 12, noise=True))
        self.assertFalse(self.run_seq([58] * 12, noise=True))          # 手を上げたまま動かさない

    def test_single_motion(self):
        self.assertFalse(self.run_seq([None, None, 60, 60, None, None, None, None, None, None]))

    def test_no_face(self):
        w = people.WaveDetector()
        t = 0.0
        for x in [55, 62] * 6:
            self.assertFalse(w.update(frame(x), None, t))
            t += 0.35

    def test_whole_frame_change(self):
        w, t, fired = people.WaveDetector(), 0.0, False
        for i in range(12):
            g = np.full((60, 80), 40 + (i % 2) * 120, np.uint8)       # 明るさが全面で変わる
            fired |= w.update(g, FACE, t)
            t += 0.35
        self.assertFalse(fired)


class PeopleTest(unittest.TestCase):
    def test_identify(self):
        d = tempfile.mkdtemp()
        p = people.People(os.path.join(d, "f.json"))
        rng = np.random.default_rng(0)
        a, b = rng.normal(size=128), rng.normal(size=128)
        p.add("たろう", [a + rng.normal(scale=.1, size=128) for _ in range(3)])
        p.add("はなこ", [b])
        self.assertEqual(p.identify(a + rng.normal(scale=.1, size=128))[0], "たろう")
        self.assertEqual(p.identify(b)[0], "はなこ")
        self.assertEqual(p.identify(rng.normal(size=128))[0], "")      # 知らない人
        self.assertEqual(oct(os.stat(p.path).st_mode & 0o777), "0o600")
        q = people.People(p.path)                                       # 再読み込み
        self.assertEqual(q.identify(a)[0], "たろう")
        self.assertTrue(q.remove("たろう"))
        self.assertEqual([x["name"] for x in q.names()], ["はなこ"])

    def test_voter(self):
        v = people.Voter()
        self.assertEqual(v.add("A"), "")
        self.assertEqual(v.add("A"), "A")
        self.assertEqual(v.add(""), "A")
        self.assertEqual(v.add("B"), "A")


if __name__ == "__main__":
    unittest.main()
