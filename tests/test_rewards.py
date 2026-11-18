"""ごほうび(称号・きょうの記録)・自動のむずかしさ・じゃんけんの手かげん・つなぎの声。"""
import json, os, random, sys, tempfile, time, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_quiz import PlayBase
import janken as jk
import playfx
import quizdata as qd
import rewards as rw
import warm as W

playfx.Stage.FAST = True


class RewardsUnit(unittest.TestCase):
    def mk(self, clock=None):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "g.json")
        return rw.Rewards(p, clock=clock or time.time), p

    def test_title_conditions_and_no_duplicates(self):
        r, p = self.mk()
        for i in range(4):
            self.assertEqual(r.record("a", "janken_win"), [])
        self.assertEqual(r.record("a", "janken_win"), ["jk5"])               # 5 回目で、はじめて
        self.assertEqual(r.record("a", "janken_win"), [])                    # 同じ称号は 2 度もらわない
        self.assertEqual(r.record("a", "janken_streak", 2), [])
        self.assertEqual(r.record("a", "janken_streak", 3), ["jk3s"])
        self.assertEqual(r.record("a", "janken_streak", 2), [])              # 連勝は「最大」を残す
        self.assertEqual(r.record("b", "janken_win"), [])                    # 機器ごと
        self.assertEqual(r.record("a", "daikichi"), ["omd"])
        self.assertEqual(r.record("a", "riddle_perfect"), ["rdp"])
        self.assertEqual(r.record("a", "unknown"), [])
        self.assertEqual(r.record("", "janken_win"), [])

    def test_pending_taken_one_at_a_time(self):
        r, p = self.mk()
        r.record("a", "daikichi")
        r.record("a", "riddle_perfect")
        t1, t2 = r.take_pending("a"), r.take_pending("a")
        self.assertEqual([t1[0], t2[0]], ["omd", "rdp"])
        self.assertIsNone(r.take_pending("a"))                               # 祝ったものは、もう出ない

    def test_saved_per_device_and_reloaded_without_duplicates(self):
        r, p = self.mk()
        for _ in range(5):
            r.record("a", "janken_win")
        r.record("b", "daikichi")
        r2 = rw.Rewards(p)
        self.assertEqual(r2.titles("a"), ["じゃんけん じょうず"])
        self.assertEqual(r2.titles("b"), ["おみくじ だいきち"])
        self.assertEqual(r2.record("a", "janken_win"), [])                    # 再起動のあとも、通算が続き、同じ称号は出ない
        self.assertEqual(r2.take_pending("a")[0], "jk5")                     # 祝う前に再起動しても、祝いは残る
        r3 = rw.Rewards(p)
        self.assertIsNone(r3.take_pending("a"))

    def test_shares_file_with_other_records(self):
        r, p = self.mk()
        json.dump({"best": 7, "janken": {"wins": 3}}, open(p, "w"))
        r.record("a", "plays")
        d = json.load(open(p))
        self.assertEqual(d["best"], 7)
        self.assertEqual(d["janken"]["wins"], 3)
        self.assertIn("rewards", d)

    def test_corrupt_file_is_ignored(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "g.json")
        open(p, "w").write("{broken")
        r = rw.Rewards(p)
        self.assertEqual(r.record("a", "plays"), [])
        self.assertEqual(r.today_plays("a"), 1)

    def test_today_resets_on_a_new_day(self):
        t = [1_800_000_000.0]
        r, p = self.mk(clock=lambda: t[0])
        r.record("a", "plays"); r.record("a", "plays")
        self.assertEqual(r.today_plays("a"), 2)
        t[0] += 86400 * 1.5
        self.assertEqual(r.today_plays("a"), 0)
        r.record("a", "plays")
        self.assertEqual(r.today_plays("a"), 1)
        self.assertEqual(r.dev["a"]["n"]["plays"], 3)                         # 通算は残る

    def test_diff_is_clamped_and_saved(self):
        r, p = self.mk()
        self.assertEqual(r.get_diff("a"), 1)
        r.set_diff("a", 9); self.assertEqual(r.get_diff("a"), 2)
        r.set_diff("a", -4); self.assertEqual(r.get_diff("a"), 0)
        self.assertEqual(rw.Rewards(p).get_diff("a"), 0)

    def test_lines(self):
        for nm in ("ずんだ", "めたん", "つむぎ"):
            self.assertIn("なぞなぞ はかせ", rw.title_line(nm, "なぞなぞ はかせ"))
            self.assertIn("3かい", rw.daily_line(nm, 3))
            self.assertNotRegex(rw.daily_line(nm, 3), r"\{\w+\}")


class MercyTests(unittest.TestCase):
    def test_probability_shape(self):
        self.assertEqual([jk.mercy_p(i) for i in (0, 1)], [0.0, 0.0])
        self.assertGreater(jk.mercy_p(2), 0)
        self.assertLess(jk.mercy_p(2), jk.mercy_p(3))
        self.assertLessEqual(jk.mercy_p(50), 0.5)

    def test_lose_run(self):
        H = lambda *r: [{"res": x} for x in r]
        self.assertEqual(jk.lose_run(H()), 0)
        self.assertEqual(jk.lose_run(H("kid_win", "robot_win", "robot_win")), 2)
        self.assertEqual(jk.lose_run(H("robot_win", "draw", "robot_win")), 2)       # あいこは とばして数える
        self.assertEqual(jk.lose_run(H("robot_win", "kid_win")), 0)

    def test_no_change_without_losing_streak(self):
        rnd = random.Random(1)
        for _ in range(200):
            self.assertEqual(jk.mercy_hand(rnd, "gu", "pa", [], 0), "pa")
            self.assertEqual(jk.mercy_hand(rnd, "gu", "pa", [], 1), "pa")

    def test_lets_kid_win_more_when_losing_and_keeps_no_three_same(self):
        rnd = random.Random(3)
        base = wins = 0
        for _ in range(3000):
            kid = rnd.choice(jk.HANDS)
            robot = jk.pick_robot_hand(rnd, [])
            base += jk.judge(kid, robot) == "robot_win"
            r2 = jk.mercy_hand(rnd, kid, robot, [], 6)
            wins += jk.judge(kid, r2) == "robot_win"
        self.assertLess(wins, base * 0.7)                                          # 負けが続く子には、ロボットの勝ちが減る
        # 同じ手を 3 回つづけて出さない(手かげんしても守る)
        for kid in jk.HANDS:
            for _ in range(300):
                hist = [{"robot": "gu", "res": "robot_win"}, {"robot": "gu", "res": "robot_win"}]
                robot = jk.pick_robot_hand(rnd, [h["robot"] for h in hist])
                self.assertNotEqual(robot, "gu")
                out = jk.mercy_hand(rnd, kid, robot, hist, 8)
                self.assertNotEqual(out, "gu")

    def test_mercy_also_when_it_would_repeat_third_time(self):
        rnd = random.Random(0)
        hist = [{"robot": "pa"}, {"robot": "pa"}]
        outs = {jk.mercy_hand(rnd, "choki", "gu", hist, 9) for _ in range(300)}
        self.assertNotIn("pa", outs)                                               # 「ぱー」を 3 回目に出す手かげんは しない


class DiffTests(PlayBase):
    def _quiz(self):
        gh, jh, qh, said, sent, _r = self._mk()
        self.assertTrue(qh.start("riddle", "", ["a"])[0])
        g = qh.games["a"]
        self._turn(g)
        return qh, g, said, sent

    def test_level_tags_cover_all_levels(self):
        self.assertEqual(len(qd.LEVELS), len(qd.RIDDLES))
        for lv in (0, 1, 2):
            self.assertGreaterEqual(qd.LEVELS.count(lv), 5)

    def test_transitions_and_clamp(self):
        qh, g, said, sent = self._quiz()
        rw.shared(g.path).set_diff("a", 1)
        g.cok = g.cng = 0
        g._adapt(True); self.assertEqual(g._diff(), 1)                              # 1 問だけでは変えない
        g._adapt(True); self.assertEqual(g._diff(), 2)                              # 2 問つづけて正解で 上がる
        g._adapt(True); g._adapt(True); self.assertEqual(g._diff(), 2)              # 上限
        g._adapt(False); self.assertEqual(g._diff(), 2)
        g._adapt(False); self.assertEqual(g._diff(), 1)                             # 2 問つづけて まちがいで 下がる
        g._adapt(True); g._adapt(False); g._adapt(True); g._adapt(False)
        self.assertEqual(g._diff(), 1)                                              # まぜこぜなら 変えない
        g._adapt(False); g._adapt(False); g._adapt(False); g._adapt(False)
        self.assertEqual(g._diff(), 0)                                              # 下限
        qh.stop("a")
        self.assertTrue(self._wait(lambda: not g.on))

    def test_questions_follow_level(self):
        qh, g, said, sent = self._quiz()
        qh.stop("a")
        self.assertTrue(self._wait(lambda: not g.on))
        for lv in (0, 1, 2):
            rw.shared(g.path).set_diff("a", lv)
            g.used = {}
            got = []
            for _ in range(8):
                it = g._make()
                idx = [r[0] for r in qd.RIDDLES].index(it.prompt)
                got.append(qd.LEVELS[idx])
            self.assertTrue(all(x == lv for x in got), (lv, got))
        rw.shared(g.path).set_diff("a", 1)
        g.used = {}
        seen = set()
        for _ in range(len([x for x in qd.LEVELS if x == 1])):                       # 同じ問題が くり返されず、出しきるまで 重ならない
            seen.add(g._make().prompt)
        self.assertEqual(len(seen), len([x for x in qd.LEVELS if x == 1]))

    def test_diff_changes_through_play(self):
        qh, g, said, sent = self._quiz()
        rw.shared(g.path).set_diff("a", 0)
        for _ in range(2):
            self.ans(qh, g, g.cur.answers[0])
            self._turn(g)
        self.assertEqual(rw.shared(g.path).get_diff("a"), 1)                        # 2 問つづけて正解 → ふつう
        qh.stop("a")
        self.assertTrue(self._wait(lambda: not g.on))


class CelebrateTests(PlayBase):
    def test_riddle_title_celebrated_once_and_saved(self):
        gh, jh, qh, said, sent, _r = self._mk()
        self.assertTrue(qh.start("riddle", "", ["a"])[0])
        g = qh.games["a"]
        R = rw.shared(g.path)
        for _ in range(8):
            R.record("a", "riddle_ok")                                               # 8 問ためてある
        self._turn(g)
        self.ans(qh, g, g.cur.answers[0])                                            # 9 問目
        self._turn(g)
        self.ans(qh, g, g.cur.answers[0])                                            # 10 問目 → 称号は、ラウンドの おわりに祝う
        for n in range(3, 6):
            self._turn(g)
            self.ans(qh, g, g.cur.answers[0])
        self.assertTrue(self._wait(lambda: any("なぞなぞ くん" in c for _n, c in said)))
        self.assertTrue(self._wait(lambda: any("もういっかい" in c or "やる" in c for _n, c in said[-4:])))
        boards = [m for n, m in sent if m.get("t") == "board" and "しょうごう" in m.get("title", "")]
        self.assertEqual(len(boards), 1)
        self.assertEqual(boards[0]["fx"], "win")
        self.assertEqual(len([1 for _n, c in said if "なぞなぞ くん" in c]), 1)           # 同じ称号を、2 回は言わない
        self.assertEqual([t for t in R.titles("a")].count("なぞなぞ くん"), 1)
        self.assertEqual(json.load(open(g.path))["rewards"]["a"]["titles"].count("rd10"), 1)
        qh.stop("a")
        self.assertTrue(self._wait(lambda: not g.on))
        self.assertEqual(self.servo_sum(sent), (0, 0))

    def test_one_celebration_per_round_even_with_two_titles(self):
        gh, jh, qh, said, sent, _r = self._mk()
        self.assertTrue(qh.start("omikuji", "", ["a"])[0])
        g = qh.games["a"]
        R = rw.shared(g.path)
        R.record("a", "omikuji", 4)                                                  # 次の 1 回で「おみくじ マスター」
        R.record("a", "daikichi")                                                    # 「おみくじ だいきち」も すでに保留
        self.assertTrue(self._wait(lambda: g.turn == "kid"))
        titles = [m for n, m in sent if m.get("t") == "board" and "しょうごう" in m.get("title", "")]
        self.assertEqual(len(titles), 1)                                             # 1 回の おみくじで、祝いは 1 つ
        self.assertTrue(self._wait(lambda: R.snapshot("a")["pending"] == 1))                 # もう 1 つは、つぎの ラウンドまで とっておく
        qh.stop("a")
        self.assertTrue(self._wait(lambda: not g.on))

    def test_daily_summary_at_most_once_per_game(self):
        gh, jh, qh, said, sent, _r = self._mk()
        self.assertTrue(qh.start("mimic", "", ["a"])[0])
        g = qh.games["a"]
        rw.shared(g.path).record("a", "plays", 1)                                    # きょう 2 回目以降
        self._turn(g)
        qh.say("やめよう", "a")
        self.assertTrue(self._wait(lambda: not g.on))
        daily = [c for _n, c in said if "きょうは" in c and "かい" in c]
        self.assertEqual(len(daily), 1)

    def test_no_daily_on_first_play(self):
        gh, jh, qh, said, sent, _r = self._mk()
        self.assertTrue(qh.start("mimic", "", ["a"])[0])
        g = qh.games["a"]
        self._turn(g)
        qh.say("やめよう", "a")
        self.assertTrue(self._wait(lambda: not g.on))
        self.assertEqual([c for _n, c in said if "きょうは" in c], [])

    def test_janken_win_records_and_streak_title(self):
        gh, jh, qh, said, sent, _r = self._mk()
        self.assertTrue(jh.start("robot", "easy", ["a"])[0])
        g = jh.games["a"]
        R = rw.shared(g.path)
        for _ in range(4):
            R.record("a", "janken_win")
        self._turn(g)
        # ロボットの手を、必ず負ける手にそろえる
        g.rnd = random.Random(0)
        orig = jk.pick_robot_hand
        jk.pick_robot_hand = lambda rnd, h: "choki"
        try:
            self.assertTrue(jh.say("ぐー", "a"))
            self.assertTrue(self._wait(lambda: any("じゃんけん じょうず" in c for _n, c in said)))
        finally:
            jk.pick_robot_hand = orig
        self.assertEqual(R.titles("a"), ["じゃんけん じょうず"])
        jh.stop("a")
        self.assertTrue(self._wait(lambda: not g.on))


class FillerTests(PlayBase):
    def _game(self):
        gh, jh, qh, said, sent, _r = self._mk()
        self.assertTrue(qh.start("mimic", "", ["a"])[0])
        g = qh.games["a"]
        qh.stop("a")
        self.assertTrue(self._wait(lambda: not g.on))
        said.clear()
        return g, said

    def test_filler_only_when_synthesis_is_slow_and_not_repeated(self):
        g, said = self._game()
        g.FILLER_AFTER = 0.05
        g._stop.clear()
        done = {"n": 0}

        class Eng:
            def __init__(e, delay): e.delay = delay; e.hot = {"えーっと…"}
            def line_cached(e, text, voice, emotion=None, raw=None): return text in e.hot
            def speech_pcm_as(e, text, voice, emotion=None, raw=None):
                if text not in e.hot:
                    time.sleep(e.delay)
                done["n"] += 1
                return b"\0" * 10
        g._synth(Eng(0.4), 0, "あたらしい ぶん", {}, None)
        self.assertEqual([c for _n, c in said if c == "えーっと…"], ["えーっと…"])        # おそいので つなぎの声
        g._synth(Eng(0.4), 0, "つぎの ぶん", {}, None)
        self.assertEqual([c for _n, c in said if c == "えーっと…"], ["えーっと…"])        # すぐあとは 連発しない
        said.clear()
        g._filler_t = -1e9
        e = Eng(0.0)
        e.hot.add("さくせいずみ")
        g._synth(e, 0, "さくせいずみ", {}, None)
        g._synth(Eng(0.0), 0, "はやい ぶん", {}, None)                                       # 速く作れたなら つなぎは不要
        self.assertEqual([c for _n, c in said if c == "えーっと…"], [])

    def test_filler_needs_prewarmed_filler_and_propagates_errors(self):
        g, said = self._game()
        g.FILLER_AFTER = 0.05
        g._stop.clear()

        class Cold:
            def line_cached(e, *a, **k): return False
            def speech_pcm_as(e, *a, **k): time.sleep(0.2); return b"\0"
        g._synth(Cold(), 0, "ぶん", {}, None)
        self.assertEqual([c for _n, c in said if c == "えーっと…"], [])                     # つなぎの声が作り置きに無いなら、鳴らさない

        class Boom:
            def line_cached(e, *a, **k): return False
            def speech_pcm_as(e, *a, **k): raise RuntimeError("x")
        with self.assertRaises(RuntimeError):
            g._synth(Boom(), 0, "ぶん", {}, None)

    def test_unclear_is_not_repeated_the_same_way(self):
        g, said = self._game()
        a = g.L("unclear")
        b = g.L("unclear")
        self.assertIn(b, [tone for tone in (playfx.tone(g.name, x) for x in playfx.COMMON["unclear2"])])
        self.assertNotIn("もういっかい", b)


class WarmIncludesNewLines(unittest.TestCase):
    def test_new_lines_are_prewarmed(self):
        plan = {p[1] for p in W.build_plan(["ずんだ", "めたん", "つむぎ"])}
        self.assertIn("えーっと…", plan)
        self.assertIn(rw.title_line("ずんだ", "なぞなぞ はかせ"), plan)
        self.assertIn("きょうは 3かい あそんだのだ！たのしかったのだ！", plan)
        self.assertIn("ぐー、ちょき、ぱーの どれかな？", plan)
        for t in playfx.COMMON["unclear2"]:
            self.assertTrue(any(x.startswith(t[:6]) for x in plan))


if __name__ == "__main__":
    unittest.main()


class LineQualityTests(unittest.TestCase):
    """子ども向けの台詞の点検: 記号の取りこぼし・むずかしい言葉・読みまちがえやすい漢字・口調の文法。"""
    HARD = ("せんしゅ", "むてき", "れんぞく", "ほうこう", "ゆうしょう", "ちょうせん", "さいこう きろく")

    def templates(self):
        import fun, quiz
        out = set()
        def walk(o):
            if isinstance(o, str): out.add(o)
            elif isinstance(o, dict): [walk(v) for v in o.values()]
            elif isinstance(o, (list, tuple)): [walk(v) for v in o]
        for d in (fun.HOI, fun.OMIKUJI, fun.MIMIC, quiz.LINES, jk.JK, playfx.COMMON, fun.OMIKUJI_MSG):
            walk(d)
        return out

    def test_no_hard_words(self):
        for t in self.templates():
            for w in self.HARD:
                self.assertNotIn(w, t)

    def test_tone_tokens_resolve_and_grammar(self):
        bad = ("のだかい", "ですもん", "さすです", "あそんだです", "むいたです", "ついかったです", "でたです", "だったです", "まってるです", "ですよよ", "よよ", "いいなのだ", "いいだよ", "ないなのだ")
        for nm in ("ずんだ", "めたん", "つむぎ"):
            for t in self.templates():
                u = playfx.tone(nm, t.replace("{cheer}", "").replace("{oops}", "").replace("{n}もん", "3もん").replace("{n}かい", "3かい"))
                self.assertNotRegex(u, r"\{(d|n|v|me)\}")
                for b in bad:
                    self.assertNotIn(b, u, (nm, t))
        self.assertEqual(playfx.tone("ずんだ", "{me}の かち{d}！いっぱい あそんだ{v}！"), "ぼくの かちなのだ！いっぱい あそんだのだ！")
        self.assertEqual(playfx.tone("めたん", "{me}の かち{d}！いっぱい あそんだ{v}！たのしかった{n}"), "わたしの かちです！いっぱい あそんだね！たのしかったです")
        self.assertEqual(playfx.tone("つむぎ", "{me}の かち{d}！あそんだ{v}"), "わたしの かちだよ！あそんだよ")

    def test_persona_first_person(self):
        import fun
        for nm, me in (("めたん", "ぼく"), ("つむぎ", "ぼく")):
            for scene, tpls in fun.HOI.items():
                for t in tpls:
                    self.assertNotIn(me, playfx.tone(nm, t.replace("{cheer}", "").replace("{oops}", "")), (nm, t))

    def test_yomi_makes_everything_hiragana_in_warm_plan(self):
        import re
        self.assertEqual(playfx.yomi("こえで言って、手を見せて。きみの勝ち"), "こえでいって、てをみせて。きみのかち")
        self.assertEqual(playfx.yomi("拍手"), "拍手")                                          # 熟語の一部は変えない
        for _p, text, _e, _r in W.build_plan(["ずんだ", "めたん", "つむぎ"]):
            self.assertIsNone(re.search(r"[一-鿿]", text), text)
