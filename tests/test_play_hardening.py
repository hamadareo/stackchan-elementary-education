"""遊びの作り込み: 競合・切り替え・聞き取りのゆれ・ファズ・3台同時の通しテスト。"""
import os, random, sys, tempfile, threading, time, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_quiz import PlayBase                                   # 擬似機器(Session/Hub/Engine)の土台を流用
import fun
import janken as jk
import kidchat as kc
import playfx
import quiz as q
import shiritori as sh

playfx.Stage.FAST = True


class KidHub:
    """KidChat を動かす最小の Hub(PlayBase の hub に、子どもモードの設定を足す)。"""


def mk_kid(base, hub, gh, jh, qh):
    hub.kid_on, hub.edu_on, hub.kid_auto = True, True, True
    hub.teacher_only = False
    hub.menu_acts = list(kc.ACT_DEFAULT)
    hub.game, hub.janken, hub.quiz = gh, jh, qh
    hub.discussing = lambda: False
    hub.auto = None
    hub.listen = False
    hub.discuss = base.ds if hasattr(base, "ds") else None
    return kc.KidChat(hub, clock=time.time)


class HearingTests(unittest.TestCase):
    def test_janken_hand_variants(self):
        for t, h in [("ぐー", "gu"), ("グー", "gu"), ("ぐう", "gu"), ("ぐ", "gu"), ("ぐぅ", "gu"), ("いし", "gu"), ("グーだよ", "gu"),
                     ("ちょき", "choki"), ("チョキ", "choki"), ("ちょっき", "choki"), ("チョキチョキ", "choki"), ("はさみ", "choki"),
                     ("ぱー", "pa"), ("パー", "pa"), ("ぱあ", "pa"), ("はー", "pa"), ("かみ", "pa"), ("パーだよ", "pa")]:
            self.assertEqual(jk.parse_hand(t), h, t)
        for t in ("ばいばい", "くま", "ちょっと待って", "じゃんけんぽん", "ぐーちょきぱー", "", "ありがとう", "ちがう", "ぱんだ"):   # 手ではない言葉は決めない
            self.assertIsNone(jk.parse_hand(t), t)

    def test_janken_quit_words(self):
        for t in ("やめよう", "ばいばい", "おわり", "つまんない"):
            self.assertTrue(jk.QUIT.search(t), t)

    def test_shiritori_reading_ゆれ(self):
        self.assertEqual(sh.kana_only("りんごー"), "りんご")           # 言い終わりののばす棒は落とす
        self.assertEqual(sh.kana_only("りんごっ"), "りんご")
        self.assertEqual(sh.kana_only("けーき"), "けーき")             # 言葉の中ののばす棒は残す
        self.assertEqual(sh.kana_only("あー"), "あー")                 # 短い聞き取りは、そのまま
        self.assertEqual(sh.clean_word("えっと、ぼくはりんご"), "りんご")
        self.assertEqual(sh.clean_word("こたえはうさぎ"), "うさぎ")
        self.assertEqual(sh.last_char("ゆきだるま"), "ま")
        self.assertEqual(sh.last_char("ちきゅうー"), "う")
        self.assertEqual(sh.last_char("ひこうき"), "き")
        self.assertEqual(sh.last_char("じゃんぱー"), "ぱ")
        self.assertEqual(sh.last_char("ばっ"), "ば")

    def test_dir_variants(self):
        for t, d in [("うえ", "up"), ("上", "up"), ("ウエ", "up"), ("うーえ", "up"), ("うえー", "up"), ("した", "down"), ("シタ", "down"),
                     ("ひだり", "left"), ("ヒダリ", "left"), ("みぎ", "right"), ("ミギ", "right"), ("みーぎ", "right")]:
            self.assertEqual(fun.parse_dir(t), d, t)
        for t in ("ほい", "ホイ", "ほーい", "", "あっちむいてほい", "ぐー"):
            self.assertIsNone(fun.parse_dir(t), t)

    def test_riddle_answer_variants(self):
        it = q.Item("x", "たまご", answers=["たまご", "卵"], hint="h")
        for t in ("たまご", "タマゴ", "卵", "たまごー", "えっと、たまごだよ", "たまごかな？", "答えは、たまご"):
            self.assertEqual(it.judge(t), "ok", t)
        for t in ("ごまた", "ぱん", "ねこ"):
            self.assertEqual(it.judge(t), "ng", t)


class FuzzTests(PlayBase):
    ALPHA = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをんぁぃゃゅょっーガギグゲゴアイウエオ上下左右山川海空0123456789!?！？、。…~〜 \n\t🙂😀🍎漢字記号＠＃＄％＆（）[]{}<>/\\\"'"

    def rtext(self, rnd):
        r = rnd.random()
        if r < 0.05:
            return ""
        if r < 0.1:
            return "あ" * rnd.randint(200, 3000)
        if r < 0.15:
            return "\u0000퟿‮" + "x" * rnd.randint(0, 50)
        return "".join(rnd.choice(self.ALPHA) for _ in range(rnd.randint(1, 40)))

    def test_pure_functions_never_crash(self):
        rnd = random.Random(7)
        for _ in range(3000):
            t = self.rtext(rnd)
            jk.parse_hand(t); fun.parse_dir(t); fun.clean_echo(t); sh.clean_word(t); sh.kana_only(t); sh.judge(sh.kana_only(t), "あ", set())
            playfx.fold(t); kc.worth_answering(t); kc.quiz_kind(t)
            q.Item("x", "あ", answers=["あ", "ああ"]).judge(t)
            q.Item("x", "たまご", answers=["たまご"]).judge(t)
            kc.KidChat.want(kc.KidChat.__new__(kc.KidChat), t)
            playfx.YES.search(t); playfx.NO.search(t); playfx.QUIT.search(t)

    def _fuzz_game(self, kind, hub_idx, seed):
        gh, jh, qh, said, sent, raws = self._mk(seed)
        hubs = {"shiritori": gh, "janken": jh}
        h = hubs.get(kind, qh)
        ok = h.start("robot", "easy", ["a"])[0] if kind in hubs else h.start(kind, "", ["a"])[0]
        self.assertTrue(ok)
        g = h.games["a"]
        rnd = random.Random(seed)
        for _ in range(25):
            if not g.on:
                break
            self._wait(lambda: g.turn == "kid" or not g.on, 200)
            if g.on:
                h.say(self.rtext(rnd), "a")
                time.sleep(0.01)
        h.stop("a")
        self.assertTrue(self._wait(lambda: not g.on, 400), kind)          # どんな入力でも、止められる・落ちない
        self.assertFalse(g.msg.startswith("エラー"), (kind, g.msg))
        self.assertEqual(self.servo_sum(sent), (0, 0), kind)              # 首は必ず正面に戻る

    def test_fuzz_each_game(self):
        for i, kind in enumerate(("shiritori", "janken", "riddle", "hoi", "omikuji", "mimic")):   # あっちむいてホイは選べなくしたが、中身は壊れていないことを確かめ続ける
            self._fuzz_game(kind, i, 100 + i)


class ConcurrencyTests(PlayBase):
    def test_double_start_is_exclusive(self):
        gh, jh, qh, said, sent, _r = self._mk()
        results = []
        ths = [threading.Thread(target=lambda h=h, a=a: results.append(h.start(*a)[0])) for h, a in
               [(gh, ("robot", "easy", ["a"])), (jh, ("robot", "easy", ["a"])), (qh, ("riddle", "", ["a"])), (qh, ("hoi", "", ["a"])), (gh, ("robot", "easy", ["a"]))]]
        [t.start() for t in ths]
        [t.join() for t in ths]
        self.assertEqual(sum(results), 1)                                  # 同じ機器で、同時に 2 つ以上は始まらない
        for h in (gh, jh, qh):
            h.stop(None)
        self.assertTrue(self._wait(lambda: not (gh.on or jh.on or qh.on), 400))

    def test_three_devices_different_games_independent(self):
        gh, jh, qh, said, sent, _r = self._mk()
        self.assertTrue(gh.start("robot", "easy", ["a"])[0])
        self.assertTrue(jh.start("robot", "easy", ["b"])[0])
        self.assertTrue(qh.start("omikuji", "", ["c"])[0])
        self.assertFalse(qh.start("riddle", "", ["a"])[0])                 # a は しりとり中
        ga, gb, gc = gh.games["a"], jh.games["b"], qh.games["c"]
        self.assertTrue(self._wait(lambda: ga.on and gb.on and gc.on))
        gh.stop("a")
        self.assertTrue(self._wait(lambda: not ga.on, 400))
        self.assertTrue(gb.on and gc.on)                                    # a を止めても b・c は続く
        self.assertTrue(qh.start("mimic", "", ["a"])[0])                    # a には、すぐ別の遊びを始められる
        for h in (gh, jh, qh):
            h.stop(None)
        self.assertTrue(self._wait(lambda: not (gh.on or jh.on or qh.on), 600))
        for dev in "abc":
            self.assertEqual(self.servo_sum(sent, dev), (0, 0))

    def test_disconnect_mid_game_ends_it(self):
        gh, jh, qh, said, sent, _r = self._mk()
        self.assertTrue(qh.start("riddle", "", ["a"])[0])
        g = qh.games["a"]
        self._wait(lambda: g.turn == "kid", 200)
        self.hub.ss.pop("a")                                                # 機器が切れた
        qh.say("ヒント", "a")
        self.assertTrue(self._wait(lambda: not g.on, 600))                  # つなぎ直しを待ったあと、遊びを終える


class KidSwitchTests(PlayBase):
    def _kid(self):
        gh, jh, qh, said, sent, raws = self._mk()
        self.hub.kid_on, self.hub.edu_on, self.hub.kid_auto = True, True, True
        self.hub.teacher_only = False
        self.hub.menu_acts = list(kc.ACT_DEFAULT)
        self.hub.game, self.hub.janken, self.hub.quiz = gh, jh, qh
        self.hub.discussing = lambda: False
        self.hub.auto = None
        self.hub.listen = False
        k = kc.KidChat(self.hub)
        self.hub.kid = k
        return gh, jh, qh, said, sent, k

    def sess(self, dev="a"):
        s = self.hub.ss[dev]
        s.dev_id = dev
        s.eng = type("E", (), {"last_voice": 0.0})()
        return s

    def test_want_requires_play_verb(self):
        k = kc.KidChat.__new__(kc.KidChat)
        self.assertEqual(k.want("じゃんけんしよう"), "janken")
        self.assertEqual(k.want("しりとりやろう"), "shiritori")
        self.assertEqual(k.want("おみくじ ひきたい"), "omikuji")
        self.assertIsNone(k.want("あっちむいてホイしよう"))                 # あっちむいてホイは、外した
        self.assertEqual(k.want("なぞなぞしよう"), "riddle")
        for t in ("じゃんけん", "しりとり", "おみくじ", "ものまね", "ぞう", "これをえいごにして", "", "あそぼう"):
            self.assertIsNone(k.want(t), t)

    def test_switch_game_by_voice(self):
        gh, jh, qh, said, sent, k = self._kid()
        s = self.sess()
        self.assertTrue(gh.start("robot", "easy", ["a"])[0])
        ga = gh.games["a"]
        self._wait(lambda: ga.turn == "kid", 200)
        self.assertFalse(k.switch(s, "しりとり"))                           # 言葉としての「しりとり」では切り替えない
        self.assertFalse(k.switch(s, "しりとりしよう"))                     # 同じ遊びのまま
        self.assertTrue(k.switch(s, "じゃんけんしよう"))
        self.assertTrue(self._wait(lambda: not ga.on and jh.games.get("a") is not None and jh.games["a"].on, 600))
        jg = jh.games["a"]
        self.assertTrue(self._wait(lambda: "a" not in k._switching, 100))             # 1 回目の切り替えが終わってから(終わる前の切り替えは、重複として無視される)
        self.assertTrue(k.switch(s, "おみくじしよう"))
        self.assertTrue(self._wait(lambda: not jg.on and qh.games.get("a") is not None and qh.games["a"].on, 600))
        self.assertEqual(k.current("a"), "omikuji")
        self.hub.menu_acts = ["janken", "omikuji"]                           # 出さない遊びへは切り替えない
        self.assertFalse(k.switch(s, "なぞなぞしよう"))
        for h in (gh, jh, qh):
            h.stop(None)
        self.assertTrue(self._wait(lambda: not (gh.on or jh.on or qh.on), 600))

    def test_other_device_game_does_not_block_this_device(self):
        gh, jh, qh, said, sent, k = self._kid()
        self.assertTrue(jh.start("robot", "easy", ["a"])[0])
        self.assertEqual(k.blocked("b"), "")                                 # a が遊んでいても b は自由
        self.assertNotEqual(k.blocked("a"), "")
        s = self.sess("b")
        self.assertTrue(k.hear(s, "なぞなぞしよう", "ja"))                    # b で遊びを始められる
        self.assertTrue(self._wait(lambda: qh.games.get("b") is not None and qh.games["b"].on, 400))
        for h in (gh, jh, qh):
            h.stop(None)
        self.assertTrue(self._wait(lambda: not (gh.on or jh.on or qh.on), 600))

    def test_edu_off_and_on_state(self):
        gh, jh, qh, said, sent, k = self._kid()
        s = self.sess()
        self.hub.edu_on = False
        self.assertTrue(k.hear(s, "なぞなぞしよう", "ja"))                    # 教材モードでなくても、遊びは始められる
        self.assertTrue(self._wait(lambda: qh.games.get("a") is not None and qh.games["a"].on, 400))
        qh.stop(None)
        self.assertTrue(self._wait(lambda: not qh.on, 400))
        self.hub.edu_on = True
        self.hub.menu_acts = ["shiritori"]
        spoke = len(said)
        self.assertTrue(k.hear(s, "なぞなぞしよう", "ja"))                    # 出さない遊びは始めず、話さない
        time.sleep(0.3)
        self.assertFalse(qh.on)
        self.assertEqual(len(said), spoke)


class ScenarioTests(PlayBase):
    def test_every_game_round_trip_with_noise(self):
        """開始→雑音・無音・ほかの遊びの名前・不適切な言葉・長い発話→終了。落ちず、首・表示・顔の後始末がされる。"""
        for kind in ("riddle", "hoi", "omikuji", "mimic"):
            gh, jh, qh, said, sent, _r = self._mk(3)
            self.assertTrue(qh.start(kind, "", ["a"])[0])
            g = qh.games["a"]
            for text in ("ご視聴ありがとうございました", "", "じゃんけんしよう", "ばかばかばか", "あ" * 500, "しね", "ぐー", "ヒント", "うえ"):
                self._wait(lambda: g.turn == "kid" or not g.on, 200)
                if not g.on:
                    break
                qh.say(text, "a")
                time.sleep(0.02)
            qh.stop("a")
            self.assertTrue(self._wait(lambda: not g.on, 400), kind)
            self.assertEqual(self.servo_sum(sent), (0, 0), kind)
            boards = [m for n, m in sent if m.get("t") == "board"]
            if boards:
                self.assertTrue(boards[-1].get("clear"), kind)                # 終わったら、表示カードを消す
            self.assertEqual([m for n, m in sent if m.get("t") == "face"][-1]["state"], "neutral", kind)
            self.assertIsNone(self.hub.ss["a"].hand_cb, kind)                 # 顔追従を止めたままにしない

    def test_mimic_does_not_echo_insults(self):
        gh, jh, qh, said, sent, raws = self._mk(4)
        self.assertTrue(qh.start("mimic", "", ["a"])[0])
        g = qh.games["a"]
        self._wait(lambda: g.turn == "kid", 200)
        qh.say("ばーか", "a")
        self.assertTrue(self._wait(lambda: any("ことば" in c for _n, c in said[1:]), 400))
        self.assertFalse(any(c == "ばーか" for _n, c in said))
        qh.stop("a")
        self.assertTrue(self._wait(lambda: not g.on, 400))

    def test_hoi_quit_during_watch(self):
        gh, jh, qh, said, sent, _r = self._mk(5)
        fun.HOI_WINDOW = 3.0
        try:
            self.assertTrue(qh.start("hoi", "", ["a"])[0])
            g = qh.games["a"]
            for _ in range(60):
                if g.phase in ("watch", "ask_dir") or not g.on:
                    break
                time.sleep(0.05)
            if g.phase == "watch":
                qh.say("やめよう", "a")
                self.assertTrue(self._wait(lambda: not g.on, 300))
            qh.stop("a")
            self.assertTrue(self._wait(lambda: not g.on, 300))
            self.assertEqual(self.servo_sum(sent), (0, 0))
        finally:
            fun.HOI_WINDOW = 1.2


if __name__ == "__main__":
    unittest.main()
