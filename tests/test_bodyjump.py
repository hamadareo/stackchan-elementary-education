"""からだジャンプの検出(bodyjump.py)。"""
import os, sys, random, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))
import bodyjump as bj

FPS = 10.0
H, FH = 120.0, 36.0           # 画像の高さ / 顔の縦の長さ(160x120 の画像で、顔が 36px くらい)


def run(ys, fh=FH, det=None, noise=0.0, seed=1, none_at=()):
    det = det or bj.JumpDetector()
    rnd = random.Random(seed)
    hits = []
    for i, y in enumerate(ys):
        t = i / FPS
        yy = None if i in none_at else (None if y is None else y + rnd.uniform(-noise, noise))
        if det.update(t, yy, None if yy is None else fh, H):
            hits.append(round(t, 2))
    return hits


def standing(n, y=60.0):
    return [y] * n


def jump_profile(y0=60.0, height=40.0, frames=5):
    """上がって、下りる(放物線)。frames=空中の枚数。"""
    out = []
    for k in range(frames):
        x = (k + 1) / (frames + 1)
        out.append(y0 - height * 4 * x * (1 - x))
    return out


class JumpTests(unittest.TestCase):
    def test_standing_still_is_not_a_jump(self):
        self.assertEqual(run(standing(60), noise=2.0), [])

    def test_a_jump_is_found_once(self):
        ys = standing(15) + jump_profile() + standing(15)
        hits = run(ys, noise=1.0)
        self.assertEqual(len(hits), 1)
        self.assertGreaterEqual(hits[0], 1.5)

    def test_two_jumps_are_two(self):
        ys = standing(15) + jump_profile() + standing(15) + jump_profile() + standing(10)
        self.assertEqual(len(run(ys, noise=1.0)), 2)

    def test_small_hop_is_ignored(self):
        ys = standing(15) + jump_profile(height=6) + standing(15)
        self.assertEqual(run(ys, noise=1.0), [])

    def test_slow_rise_is_ignored(self):
        """背のび・近づく: ゆっくり上がっても、ジャンプではない。"""
        ys = standing(15) + [60 - 0.6 * i for i in range(60)]
        self.assertEqual(run(ys, noise=1.0), [])

    def test_sitting_down_is_not_a_jump(self):
        ys = standing(15) + jump_profile(height=-30) + standing(15, 90.0)
        self.assertEqual(run(ys, noise=1.0), [])

    def test_jitter_only(self):
        for seed in range(20):
            self.assertEqual(run(standing(80), noise=3.5, seed=seed), [], seed)

    def test_face_leaves_the_top_during_a_jump(self):
        """顔が画面の上に出て、見えなくなっても、直前まで上がっていればジャンプ。"""
        ys = standing(15, 40.0) + [30.0, 20.0, 12.0, None, None, 40.0, 40.0, 40.0]
        self.assertEqual(len(run(ys, none_at=())), 1)

    def test_face_lost_while_standing_is_not_a_jump(self):
        ys = standing(15, 70.0) + [None] * 5 + standing(10, 70.0)
        self.assertEqual(run(ys), [])

    def test_gap_prevents_double_count(self):
        ys = standing(15) + jump_profile(frames=3, height=45) + jump_profile(frames=3, height=45)
        self.assertEqual(len(run(ys)), 1)

    def test_walking_closer_is_not_a_jump(self):
        """近づくと顔が下がって大きくなる。離れると上がるが、ゆっくり。"""
        ys = [60.0 + 0.4 * i for i in range(40)] + [76 - 0.5 * i for i in range(40)]
        self.assertEqual(run(ys, noise=1.0), [])

    def test_reset_forgets_the_past(self):
        d = bj.JumpDetector()
        run(standing(15) + jump_profile(), det=d)
        d.reset()
        self.assertEqual(run(standing(15), det=d), [])             # 前のゲームの記録が残って、すぐ反応しない


if __name__ == "__main__":
    unittest.main()


class FaceLockTests(unittest.TestCase):
    def test_find_swallows_cv_errors_and_serializes(self):
        import threading
        import numpy as np
        import stackchan_bridge as sb
        f = sb.Face.__new__(sb.Face)
        f.cv2, f.det, f.sface = object(), None, None
        active, worst = [0], [0]

        class Net:
            def setInputSize(self, s): pass
            def detect(self, img):
                active[0] += 1; worst[0] = max(worst[0], active[0])
                import time; time.sleep(0.02)
                active[0] -= 1
                raise RuntimeError("OpenCV: Assertion failed")
        f.yunet = Net()
        img = np.zeros((10, 10, 3), np.uint8)
        out = []
        ts = [threading.Thread(target=lambda: out.append(f.find(img))) for _ in range(4)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(out, [None] * 4)                     # 例外は握りつぶして「顔なし」
        self.assertEqual(worst[0], 1)                         # 同時には 1 つだけ
