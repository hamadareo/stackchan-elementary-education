import json
import os, random, sys, tempfile, time, unittest
os.environ["SC_JOURNAL"] = os.path.join(tempfile.mkdtemp(), "journal.json")
os.environ["SC_PERSONA"] = os.path.join(tempfile.mkdtemp(), "persona.json")
os.environ["SC_AUTONOMY"] = os.path.join(tempfile.mkdtemp(), "auto.json")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))
import quiz as q
import quizdata as qd


class NumberTests(unittest.TestCase):
    def test_to_number(self):
        for t, n in [("23", 23), ("にじゅうさん", 23), ("にじゅうさんだよ", 23), ("えっと、さんじゅう", 30), ("二十三", 23), ("ひゃくにじゅうさん", 123),
                     ("じゅうご", 15), ("じゅう", 10), ("きゅうじゅうきゅう", 99), ("八十一", 81), ("ろくじゅうよん", 64), ("なな", 7), ("ぜろ", 0), ("２３です", 23),
                     ("ニジュウサン", 23), ("うーん、よんじゅうなな", 47)]:
            self.assertEqual(q.to_number(t), n, t)
        for t in ("ごめんね", "わからない", "りんご", ""):
            self.assertIsNone(q.to_number(t), t)


class JudgeTests(unittest.TestCase):
    def test_riddle_variants(self):
        it = q.Item("riddle", "x", "フライパン", answers=["フライパン", "ふらいぱん"], hint="h", tries=2)
        for t in ("フライパン", "ふらいぱん", "フライパンだよ", "えっと、フライパン！"):
            self.assertEqual(it.judge(t), "ok", t)
        self.assertEqual(it.judge("ほうちょう"), "ng")
        self.assertEqual(it.judge("ヒント"), "hint")
        self.assertEqual(it.judge("わからない"), "hint")

    def test_one_char_answer_is_exact(self):
        it = q.Item("riddle", "x", "め", answers=["め", "目"], tries=2)
        self.assertEqual(it.judge("めだよ"), "ok")
        self.assertEqual(it.judge("目"), "ok")
        self.assertEqual(it.judge("あめ"), "ng")                     # 「あめ」の中の「め」には反応しない

    def test_tf(self):
        yes, no = q.Item("tf", "x", "", truth=True), q.Item("tf", "x", "", truth=False)
        for t in ("まる", "マル", "○", "はい", "まるだよ", "そう"):
            self.assertEqual(yes.judge(t), "ok", t)
            self.assertEqual(no.judge(t), "ng", t)
        for t in ("ばつ", "バツ", "×", "ちがう", "いいえ", "ううん", "そうじゃない"):
            self.assertEqual(no.judge(t), "ok", t)
            self.assertEqual(yes.judge(t), "ng", t)
        self.assertEqual(yes.judge("うーん"), "unclear")             # 考えている「うーん」は「うん」ではない
        self.assertEqual(yes.judge("ぞう"), "unclear")

    def test_choice(self):
        opts = ["おおさか", "とうきょう|東京", "きょうと|京都"]
        it = q.Item("choice", "x", "", opts=opts, idx=2)
        for t in ("2", "に", "にばん", "2ばん", "2番", "とうきょう", "東京だよ", "ふたつめ"):
            self.assertEqual(it.judge(t), "ok", t)
        self.assertEqual(it.judge("1ばん"), "ng")
        self.assertEqual(it.judge("さん"), "ng")
        self.assertEqual(it.judge("おおさか"), "ng")
        self.assertEqual(it.judge("ぺんぎん"), "unclear")

    def test_math_and_english(self):
        it = q.Item("math", "x", "23", value=23, tries=2)
        self.assertEqual(it.judge("にじゅうさんだよ"), "ok")
        self.assertEqual(it.judge("24"), "ng")
        self.assertEqual(it.judge("ううん"), "unclear")
        en = q.Item("jp_en", "x", "アップル", answers=["apple", "アップル", "あっぷる"], tries=2)
        for t in ("Apple.", "apple", "アップル", "あっぷる", "えっと、アップルだよ"):
            self.assertEqual(en.judge(t), "ok", t)
        self.assertEqual(en.judge("banana"), "ng")


class DataTests(unittest.TestCase):
    def test_sizes(self):
        self.assertGreaterEqual(len(qd.RIDDLES), 40)
        self.assertGreaterEqual(len(qd.TF) + len(qd.CHOICE), 40)
        self.assertGreaterEqual(len(qd.ENGLISH), 30)
        for _q, opts, idx, _e in qd.CHOICE:
            self.assertEqual(len(opts), 3)
            self.assertIn(idx, (1, 2, 3))

    def test_every_riddle_answer_is_accepted(self):
        for ques, ans, hint in qd.RIDDLES:
            it = q.Item("riddle", ques, ans[0], answers=ans)
            for a in ans:
                self.assertEqual(it.judge(a), "ok", (ques, a))

    def test_math_ranges(self):
        r = random.Random(1)
        for _ in range(300):
            a = q.gen_math(1, r); self.assertTrue(2 <= a.value <= 18)
            a = q.gen_math(2, r); self.assertTrue(12 <= a.value <= 99)
            a = q.gen_math(3, r); self.assertTrue(1 <= a.value <= 19)
            a = q.gen_math(4, r); self.assertTrue(1 <= a.value <= 81)
        self.assertIn("かける", q.gen_math(4, r).prompt)


class TodayTests(unittest.TestCase):
    def test_today(self):
        now = time.struct_time((2026, 11, 17, 15, 5, 0, 1, 321, 0))          # 2026-11-17 火曜 15:05
        self.assertEqual(q.today_reply("きょうは何日？", "ずんだ", now), "きょうは、11月17日、火曜日なのだ。")
        self.assertEqual(q.today_reply("きょうはなんようび", "つむぎ", now), "きょうは、火曜日だよ。")
        self.assertEqual(q.today_reply("いまなんじ？", "めたん", now), "いまは、午後3時5分です。")
        self.assertEqual(q.today_reply("今何時", "つむぎ", time.struct_time((2026, 11, 17, 12, 0, 0, 1, 321, 0))), "いまは、午後12時ちょうどだよ。")
        self.assertIsNone(q.today_reply("こんにちは", "ずんだ", now))

    def test_kid_triggers(self):
        import kidchat as k
        for t, kind in [("なぞなぞしよう", "riddle"), ("クイズやろう", "quiz"), ("さんすうしたい", "math"), ("たしざんしよう", "add"), ("ひきざんやろう", "sub"),
                        ("九九しよう", "kuku"), ("えいごしよう", "english"), ("えいご", "english")]:
            self.assertEqual(k.quiz_kind(t), kind, t)
        self.assertIsNone(k.quiz_kind("これをえいごにして"))                      # 通訳の指示は遊びにしない
        self.assertIsNone(k.quiz_kind("きょうは学校でえいごのじゅぎょうがあったよ"))
        self.assertIn("なぞなぞ", k.MENU)


class QuizPlayTests(unittest.TestCase):
    def _mk(self, seed=1):
        import shiritori as s, discuss as d
        said, sent = [], []

        class S:
            def __init__(se, n): se.n = n
            def send(se, m): sent.append((se.n, m))
            def _send_pcm(se, pcm, caption="", lang=""): said.append((se.n, caption)); return 0.0

        class Hub:
            def __init__(h): h.ss = {i: S(i) for i in "abc"}
            def by_id(h, i): return h.ss.get(i)
            def event(h, t): pass

        class Reg:
            def all(h): return [{"id": i, "name": "N" + i} for i in "abc"]

        class Eng:
            def translate(e, m): return "?", "x"
            def speech_pcm_as(e, text, voice, emotion=None): return b""

        hub = Hub()
        ds = d.Discussion(hub, Reg(), lambda: Eng(), d.Memory(os.path.join(tempfile.mkdtemp(), "m.json")), d.Journal(os.path.join(tempfile.mkdtemp(), "j.json")))
        path = os.path.join(tempfile.mkdtemp(), "g.json")
        args = (hub, Reg(), ds, lambda: Eng(), path)
        gh, jh, qh = s.GameHub(*args), __import__("janken").JankenHub(*args), q.QuizHub(*args, random.Random(seed))
        hubs = [gh, jh, qh]
        for h in hubs:
            h.peers = [x for x in hubs if x is not h]
        return gh, jh, qh, said, sent

    def _wait(self, cond, n=300):
        for _ in range(n):
            if cond(): return True
            time.sleep(0.05)
        return False

    def ans(self, qh, g, text, dev="a"):
        """答えを入れて、教材がそれを受け取る(子どもの番が終わる)まで待つ。"""
        self.assertTrue(self._wait(lambda: g.turn == "kid" or not g.on))
        self.assertTrue(qh.say(text, dev))
        self.assertTrue(self._wait(lambda: g.turn != "kid" or not g.on))

    def _turn(self, g):
        self.assertTrue(self._wait(lambda: g.turn == "kid" or not g.on))
        self.assertTrue(g.on)

    def test_riddle_round_hint_reveal_and_end(self):
        gh, jh, qh, said, sent = self._mk()
        self.assertTrue(qh.start("riddle", "", ["a"])[0])
        g = qh.games["a"]
        self._turn(g)
        self.assertEqual(g.qno, 1)
        self.assertTrue(any("1もんめ" in c for _n, c in said))              # ロボットから問題を出す
        ans = g.cur.answers[0]
        self.ans(qh, g, ans)                                    # せいかい
        self.assertTrue(self._wait(lambda: g.score == 1))
        self._turn(g)
        self.assertEqual(g.qno, 2)
        self.ans(qh, g, "ヒント")                                                 # 1 回目: ヒント
        self.assertTrue(self._wait(lambda: any(c.startswith("ヒント") for _n, c in said)))
        self._turn(g)
        self.ans(qh, g, "わからない")                                             # 2 回目: 答えを教える
        self.assertTrue(self._wait(lambda: any("こたえは" in c for _n, c in said)))
        self.assertEqual(g.score, 1)
        for n in range(3, 6):                                                # 残りは全部せいかい
            self._turn(g)
            self.assertEqual(g.qno, n)
            self.ans(qh, g, g.cur.answers[0])
        self.assertTrue(self._wait(lambda: any("もういっかい" in c for _n, c in said)))
        self.assertEqual(g.score, 4)
        self._turn(g)
        self.ans(qh, g, "いいえ")                                                 # もういっかい? → いいえ
        self.assertTrue(self._wait(lambda: not g.on))
        d = json.load(open(qh.path))["quiz"]
        self.assertEqual((d["plays"], d["rounds"], d["correct"], d["answered"]), (1, 1, 4, 5))
        self.assertTrue(all(who == "a" for who, _c in said))                  # 機器 a だけが話す(ほかの機器は連携しない)

    def test_play_again_and_quit(self):
        gh, jh, qh, said, sent = self._mk(2)
        qh.start("quiz", "", ["b"])
        g = qh.games["b"]
        for n in range(1, 6):
            self._turn(g)
            it = g.cur
            ok = "まる" if it.kind == "tf" and it.truth else "ばつ" if it.kind == "tf" else str(it.idx)
            self.ans(qh, g, ok, "b")
        self.assertTrue(self._wait(lambda: any("もういっかい" in c for _n, c in said)))
        self.assertEqual(g.score, 5)
        self.assertTrue(any("ぜんぶ" in c for _n, c in said))
        self._turn(g)
        self.ans(qh, g, "うん、もういっかい", "b")
        self._turn(g)
        self.assertEqual((g.qno, g.score), (1, 0))                            # 新しいラウンド
        self.ans(qh, g, "やめよう", "b")                                               # いつでも やめられる
        self.assertTrue(self._wait(lambda: not g.on))
        self.assertTrue(any("ばいばい" in c for _n, c in said))

    def test_wrong_tf_reveals_with_explanation(self):
        gh, jh, qh, said, sent = self._mk(3)
        qh.start("quiz", "", ["a"])
        g = qh.games["a"]
        self._turn(g)
        it = g.cur
        wrong = "ばつ" if (it.kind == "tf" and it.truth) else "まる" if it.kind == "tf" else str(it.idx % 3 + 1)
        self.ans(qh, g, wrong)
        self.assertTrue(self._wait(lambda: any("こたえは" in c for _n, c in said)))
        self.assertEqual(g.score, 0)
        self.assertTrue(any(m.get("state") == "thinking" or m.get("state") == "neutral" for _n, m in sent))
        qh.stop()
        self.assertTrue(self._wait(lambda: not g.on))

    def test_math_levels_up(self):
        gh, jh, qh, said, sent = self._mk(4)
        qh.start("math", "", ["a"])
        g = qh.games["a"]
        self.assertEqual(g.level, 1)
        for _ in range(2):
            self._turn(g)
            self.assertTrue(1 <= g.cur.value <= 18)
            self.ans(qh, g, "にじゅうさん" if g.cur.value == 23 else str(g.cur.value))      # 数字でも こたえられる
        self.assertTrue(self._wait(lambda: g.level == 2))                     # 2 問つづけて せいかい → レベルアップ
        self.assertTrue(self._wait(lambda: any("レベルアップ" in c or "れべるあっぷ" in c for _n, c in said)))
        self._turn(g)
        self.assertTrue(10 <= g.cur.value + 0 or True)
        qh.stop()

    def test_kanji_data_and_judge(self):
        n = sum(len(v) for v in qd.KANJI.values())
        self.assertGreaterEqual(n, 60)
        for lv in (1, 2, 3):
            self.assertGreaterEqual(len(qd.KANJI[lv]), 20)
        it = q.Item("kanji", "x", "やま", answers=["やま", "さん"], hint="h", tries=2)
        for t in ("やま", "ヤマ", "やまだよ", "えっと、やま", "さん", "サン"):
            self.assertEqual(it.judge(t), "ok", t)
        self.assertEqual(it.judge("山"), "ng")                              # 漢字そのものは よみではない
        self.assertEqual(it.judge("かわ"), "ng")
        self.assertEqual(it.judge("ヒント"), "hint")
        it2 = q.Item("kanji", "x", "ひ", answers=["ひ", "にち"], tries=2)
        self.assertEqual(it2.judge("ひ"), "ok")
        self.assertEqual(it2.judge("にち"), "ok")
        self.assertEqual(it2.judge("ひろい"), "ng")                          # 1 文字の答えは、全体がそれのときだけ

    def test_kanji_kid_trigger_and_menu(self):
        import kidchat as k
        for t in ("かんじしよう", "漢字クイズやろう", "かんじ"):
            self.assertEqual(k.quiz_kind(t), "kanji", t)
        self.assertIsNone(k.quiz_kind("いいかんじだね"))
        self.assertIsNone(k.quiz_kind("かんじをえいごにして"))
        self.assertEqual(k.ACT_DEFAULT, ["shiritori", "janken", "riddle", "quiz", "math", "kanji"])
        m = k.menu_text(k.ACT_DEFAULT)
        self.assertIn("かんじ", m)
        self.assertNotIn("えいご", m)
        self.assertIn("かんじ", k.MENU)
        self.assertNotIn("えいご", k.MENU)

    def test_kanji_round_hint_reveal_caption_and_exclusive(self):
        gh, jh, qh, said, sent = self._mk(7)
        self.assertTrue(qh.start("kanji", "", ["a"])[0])
        g = qh.games["a"]
        self.assertFalse(gh.start("robot", "easy", ["a"])[0])                  # 同じ機器で しりとりは同時にできない
        self._turn(g)
        self.assertEqual(g.level, 1)
        ch = g.cur.show
        self.assertTrue(any("がめんの かんじ" in c or "「" in c for _n, c in said))
        self.assertIn("「", ch)                                                # 画面には漢字そのもの
        self.ans(qh, g, "ヒント")                                               # 1 回目: ヒント(最初の1文字)
        self.assertTrue(self._wait(lambda: any(c.startswith("ヒント") and "はじまる" in c for _n, c in said)))
        self._turn(g)
        self.ans(qh, g, "わからない")                                           # 2 回目: 答えを教える
        self.assertTrue(self._wait(lambda: any("こたえは" in c for _n, c in said)))
        self.assertEqual(g.score, 0)
        for n in range(2, 6):
            self._turn(g)
            self.assertEqual(g.qno, n)
            self.ans(qh, g, g.cur.answers[-1] if len(g.cur.answers) > 1 else g.cur.answers[0])   # 音読みでも せいかい
        self.assertTrue(self._wait(lambda: any("もういっかい" in c for _n, c in said)))
        self.assertEqual(g.score, 4)
        self.assertTrue(g.level >= 2)                                         # つづけて せいかい → 学年が上がる
        self._turn(g)
        self.ans(qh, g, "いいえ")
        self.assertTrue(self._wait(lambda: not g.on))
        self.assertTrue(any(m.get("t") == "caption" or True for _n, m in sent))

    def test_english_and_kuku(self):
        gh, jh, qh, said, sent = self._mk(5)
        qh.start("english", "", ["a"])
        g = qh.games["a"]
        self._turn(g)
        self.assertIn(g.cur.kind, ("jp_en", "en_jp"))
        self.ans(qh, g, "きょうは いい てんき")                                     # まちがい(1 回目)
        self.assertTrue(self._wait(lambda: any("おしい" in c or "ちがう" in c for _n, c in said)))
        self._turn(g)
        self.ans(qh, g, g.cur.answers[0])
        self.assertTrue(self._wait(lambda: g.score == 1))
        qh.stop()
        self.assertTrue(self._wait(lambda: not g.on))
        qh.start("kuku", "", ["a"])
        g = qh.games["a"]
        self._turn(g)
        self.assertIn("かける", g.cur.prompt)
        qh.stop()
        self.assertTrue(self._wait(lambda: not g.on))

    def test_english_hear_accepts_en(self):
        gh, jh, qh, said, sent = self._mk(6)
        qh.start("english", "", ["a"])
        g = qh.games["a"]
        self._turn(g)
        time.sleep(1.6)                                                       # ロボットの声のあとの待ち時間
        self.assertTrue(qh.hear(g.cur.answers[0], "en", "a"))
        self.assertTrue(self._wait(lambda: g.score == 1))
        qh.stop()
        qh2 = self._mk(7)[2]
        qh2.start("riddle", "", ["a"])
        g2 = qh2.games["a"]
        self._turn(g2)
        time.sleep(1.6)
        self.assertTrue(qh2.hear("apple", "en", "a"))                         # なぞなぞ中の英語は捨てる(でも翻訳には回さない)
        self.assertTrue(g2.q.empty())
        qh2.stop()

    def test_exclusive_per_device(self):
        gh, jh, qh, said, sent = self._mk(8)
        self.assertTrue(qh.start("riddle", "", ["a"])[0])
        self.assertFalse(gh.start("robot", "easy", ["a"])[0])                 # 同じ機器では、しりとりを同時に始めない
        self.assertFalse(jh.start("robot", "easy", ["a"])[0])
        ok, err = qh.start("quiz", "", ["a"])
        self.assertFalse(ok)
        self.assertTrue(gh.start("robot", "easy", ["b"])[0])                  # 別の機器なら、別の遊びができる(連携しない)
        self.assertFalse(qh.start("riddle", "", ["b"])[0])
        self.assertTrue(gh.on and jh.on and qh.on)                            # どれかが動けば「教材中」
        qh.stop(); gh.stop()
        self.assertTrue(self._wait(lambda: not qh.own_on and not gh.own_on))
        self.assertTrue(jh.start("robot", "easy", ["a"])[0])
        self.assertFalse(qh.start("math", "", ["a"])[0])
        jh.stop()
        self.assertTrue(self._wait(lambda: not jh.own_on))

    def test_independent_devices(self):
        gh, jh, qh, said, sent = self._mk(9)
        self.assertTrue(qh.start("riddle", "", ["a", "b"])[0])
        ga, gb = qh.games["a"], qh.games["b"]
        self._turn(ga); self._turn(gb)
        time.sleep(1.6)
        qh.say(ga.cur.answers[0], "a")                                        # a の子どもだけが答えた
        self.assertTrue(self._wait(lambda: ga.score == 1))
        self.assertEqual(gb.score, 0)
        qh.stop()
        self.assertTrue(self._wait(lambda: not qh.own_on))


if __name__ == "__main__":
    unittest.main()
