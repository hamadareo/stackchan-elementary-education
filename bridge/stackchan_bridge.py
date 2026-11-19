#!/usr/bin/env python3
"""StackChan ⇄ Sokutsu ブリッジ(Mac で動く)。

StackChan(CoreS3)は「耳・口・顔・首」だけの薄い端末。重い処理(音声区切り・認識・翻訳・
読み上げの音づくり・顔検出)は全部ここ(Mac)と、Mac 経由の s109 でやる。
  マイク声 ──WS──▶ VAD/区切り → 耳(s109 GPU / このMac) → 翻訳(s109 GPU / このMac) → 読み上げの音
  カメラ JPEG ──▶ 顔検出 → 首の目標(look)
  ◀── 字幕・表情・首・音声(PCM) ──

Sokutsu(tsuyaku リポジトリ)の部品を import して使う(読み取りだけ。tsuyaku 側は触らない)。
  PYTHONPATH=/Users/work/tsuyaku/src  bridge/.venv/bin/python bridge/stackchan_bridge.py

プロトコルは README.md の「通信仕様」を参照。
"""
from __future__ import annotations

import builtins as _b, time as _t
_print_orig = _b.print


def print(*a, **k):  # noqa: A001 - ログに時刻を付ける(夜間の調べものがしやすい)
    _print_orig(_t.strftime("%H:%M:%S"), *a, **k)


import argparse
import asyncio
import io
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import collections
import shutil
import threading
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import admin  # noqa: E402  管理画面
import kidchat  # noqa: E402  子どもモード
import devices as _devices  # noqa: E402  機器台帳(顔の種類の表)

sys.path.insert(0, os.environ.get("TSUYAKU_SRC", "/Users/work/tsuyaku/src"))

SR = 16000
TAG_AUDIO, TAG_JPEG, TAG_SPEECH, TAG_AUDIO_SEQ, TAG_SCREEN = 0x01, 0x02, 0x03, 0x04, 0x05   # 端末→Mac: 1,2 / Mac→端末: 3
DEVNAME = os.environ.get("SC_DEVICE_NAME", "Mac")      # 上部バーに出す「自分の機器」の名前(Ubuntu なら Ubu)
SAY_VOICE = {"ja": "Kyoko", "ko": "Yuna", "en": "Samantha", "zh": "Tingting"}


# ---------------------------------------------------------------- 共有モデル(起動時に1回)
# 声ごとの聞き取りやすさの調整。ずんだもん(標準)は、高めで速く、子どもや教室の騒音では聞き取りにくいので、遅く・低く・抑揚を抑え・音量を上げる。
# emo は、感情による声の変化をどれだけ効かせるか(1=そのまま)。感情が強いほど聞き取りにくくなるので、ずんだは弱める。
VOICE_TUNE = {"voicevox:3": {"speed": 0.88, "pitch": -0.05, "intonation": 0.9, "volume": 1.2, "emo": 0.35}}


def tuned_prosody(voice: "str | None", emotion: "dict | None") -> "dict | None":
    """感情の調子(emotion)に、その声の調整(VOICE_TUNE)を合わせる。どちらも無ければ None(いつもの声)。"""
    tune = VOICE_TUNE.get(voice or "")
    if not tune:
        return emotion or None
    k = tune.get("emo", 1.0)
    e = emotion or {}
    return {"speed": tune["speed"] * (1 + (e.get("speed", 1.0) - 1) * k), "pitch": tune["pitch"] + e.get("pitch", 0.0) * k,
            "intonation": tune["intonation"] * (1 + (e.get("intonation", 1.0) - 1) * k), "volume": tune["volume"] * (1 + (e.get("volume", 1.0) - 1) * k)}


class Engine:
    """Sokutsu の耳・翻訳・声を1か所に持つ。接続ごとの Session が使い回す。"""

    EAR_TIMEOUT = 1.5          # s109 の耳の返事を待つ秒(GPU は 0.1〜0.3 秒。届かなければ、すぐこのサーバーの耳 SenseVoice に落ちる)
    LLM_BAD_S = 30.0           # 翻訳が失敗した置き場所は、この秒数は飛ばして次(このサーバー)へ直接行く

    def __init__(self, engine: str = "auto"):
        import sherpa_onnx
        from tsuyaku.config import engine_candidates, load_settings
        from tsuyaku.stt import RemoteSTT, make_local_stt
        from tsuyaku.translate import pick_model, warmup

        self.s = s = load_settings()
        conf = sherpa_onnx.VadModelConfig()
        conf.silero_vad.model = s.silero_vad
        conf.silero_vad.threshold = 0.5
        conf.silero_vad.min_speech_duration = s.min_speech_s
        conf.sample_rate = SR
        self.vad_conf = conf
        self.sherpa = sherpa_onnx

        # 耳: s109 が答えれば s109、だめならこのMac(RemoteSTT が自動で落とす)
        local = make_local_stt(s)
        self.local_stt, self.engine_arg = local, engine
        self.stt_lock = threading.Lock()
        self.remote_ear = False
        if s.stt_url:
            try:
                import requests
                self.remote_ear = requests.get(s.stt_url.rstrip("/") + "/health", timeout=2).ok
            except Exception:  # noqa: BLE001
                self.remote_ear = False
        self.stt = RemoteSTT(s.stt_url, local, timeout=self.EAR_TIMEOUT) if self.remote_ear else local
        print(f"[bridge] 耳: {'s109 GPU(落ちたらこのMac)' if self.remote_ear else 'このMac'}", flush=True)

        # 翻訳: s109 を先に試し、届かなければこのMac(engine_candidates の順)
        self.llm: "list[tuple[str, str, str]]" = []
        for label, base, pref in engine_candidates(engine, s.host, None, None):
            try:
                model = pick_model(base, pref)
                self.llm.append((label, base, model))
            except Exception as e:  # noqa: BLE001
                print(f"[bridge] 翻訳 {label} は使えません: {e}", flush=True)
        self.llm_all = list(self.llm)                           # 管理画面から s109 / このサーバー を切り替えるための元の一覧
        if not self.llm:
            raise SystemExit("翻訳サーバーに届きません。s109 のトンネルか LM Studio を確認してください。")
        label, base, model = self.llm[0]
        print(f"[bridge] 翻訳: {label} / {model}", flush=True)
        try:
            warmup(base, model, timeout=60.0)
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] ウォームアップ失敗(続行): {e}", flush=True)

        # 声: Kokoro(en/zh) があれば使い、無ければ・ja/ko は macOS say
        self.tts = None
        try:
            from tsuyaku.tts import TTS
            self.tts = TTS(s.tts_dir_kokoro, s.tts_dir_ko, s.tts_sids, s.tts_ja_engine,
                           voices=s.tts_voice, speeds=s.tts_speed, voicevox_url=s.voicevox_url)
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] Sokutsu の声を読めません。macOS say を使います: {e}", flush=True)
        self._say_lock = threading.Lock()
        self._voice_lock = threading.Lock()
        self.prosody = None                 # 感情の声の調子(読み上げの間だけ入る)
        self.face = Face()
        if self.tts is not None:                              # 最初の読み上げが遅くならないよう、声を一度温めておく
            try:
                self.tts._synth("あ", "ja", 1.0); self.tts._synth("ok", "en", 1.0)
            except Exception:  # noqa: BLE001
                pass
        # ずんだもん(VOICEVOX ノーマル=3)。Sokutsu 本体の設定(config.json)は書き換えず、ブリッジ内だけで上書き
        self.voice_ja = os.environ.get("SC_VOICE_JA", "voicevox:3")
        if self.tts is not None:
            self.tts.voices = {**dict(s.tts_voice or {}), "ja": self.voice_ja}
        # 話速: Sokutsu の PaceController(話し手のペアと内容の量から決める)。基準は等速(設定の速い値は使わない)
        from tsuyaku.pace import PaceController
        self.pace = PaceController(priority="clear")
        self.last_speed = 1.0
        self._pcm_cache: "collections.OrderedDict" = collections.OrderedDict()
        self._line_cache: "collections.OrderedDict" = collections.OrderedDict()      # 声の作り置き: キー → (音, 先読みか)
        self._line_bytes = {True: 0, False: 0}
        self._line_lock = threading.Lock()
        self.speed_scale = 1.1                                 # 管理画面の「話速」(1.0 が標準。教室では少しだけ速い 1.1 を既定にする)

    def watch(self, every: float = 3.0) -> None:
        """s109 が後から見つかったら(再起動直後でネットワークが未接続だったときなど)GPU に切り替える。
        逆に、s109 が落ちたら、すぐこのサーバーの耳(SenseVoice)に切り替える(次の発話を待たせない)。"""
        import requests
        from tsuyaku.config import engine_candidates
        from tsuyaku.stt import RemoteSTT
        from tsuyaku.translate import pick_model
        while True:
            time.sleep(every)
            try:
                if self.remote_ear and self.s.stt_url:
                    try:
                        up = requests.get(self.s.stt_url.rstrip("/") + "/health", timeout=1.0).ok
                    except Exception:  # noqa: BLE001
                        up = False
                    if not up:
                        self.stt = self.local_stt                      # s109 が落ちた: このサーバーの耳へ(戻ってきたら、下で GPU に戻す)
                        self.remote_ear = False
                        print("[bridge] 耳: s109 に届かないので、このサーバー(SenseVoice)に切り替えました", flush=True)
                back = False
                if not self.remote_ear and self.s.stt_url:
                    try:
                        back = requests.get(self.s.stt_url.rstrip("/") + "/health", timeout=1.0).ok
                    except Exception:  # noqa: BLE001 - 届かないのは普通(オフライン)。翻訳側の見張りは続ける
                        back = False
                if back:
                    self.stt = RemoteSTT(self.s.stt_url, self.local_stt, timeout=self.EAR_TIMEOUT)
                    self.remote_ear = True
                    po = getattr(self, "pair_override", None)
                    if po:
                        self.stt.langs = po
                    print("[bridge] 耳: s109 GPU に切り替えました", flush=True)
                bad = self.__dict__.setdefault("_llm_bad", {})
                if len(self.llm) > 1:                                  # 翻訳の置き場所を先に確かめる: 届くなら s109 を使い、届かないなら最初からこのサーバーへ
                    for label, base, _m in self.llm:
                        try:
                            ok = requests.get(base.rstrip("/") + "/v1/models", timeout=1.0).ok
                        except Exception:  # noqa: BLE001
                            ok = False
                        if ok:
                            bad.pop(label, None)
                        else:
                            bad[label] = time.time() + every + 2.0
                have = {c[0] for c in self.llm_all}
                found = []
                for label, base, pref in engine_candidates(self.engine_arg, self.s.host, None, None):
                    if label in have:
                        continue
                    try:
                        found.append((label, base, pick_model(base, pref)))
                    except Exception:  # noqa: BLE001
                        pass
                if found:
                    order = {c[0]: i for i, c in enumerate(engine_candidates(self.engine_arg, self.s.host, None, None))}
                    self.llm_all = sorted(self.llm_all + found, key=lambda c: order.get(c[0], 99))
                    admin.HUB.set_mode(admin.HUB.mode)
                    print(f"[bridge] 翻訳: {', '.join(c[0] for c in found)} が使えるようになりました", flush=True)
            except Exception:  # noqa: BLE001 - 見張りは落とさない
                pass

    def voice_name(self, lang: str) -> str:
        v = (getattr(self.tts, "voices", None) or {}).get(lang, "") if self.tts is not None else ""
        if v.startswith("voicevox"):
            return "VOICEVOX " + (admin.HUB.voice_label(v.split(":")[-1]) or "")
        if v.startswith("kokoro"):
            return "Kokoro"
        return v or ("say" if sys.platform == "darwin" else "-")

    def stt_name(self) -> str:
        return "faster-whisper large-v3-turbo" if self.remote_ear else "SenseVoice-Small"

    def llm_order(self) -> list:
        """翻訳を試す順。直前に失敗した置き場所(s109 が無いときなど)は、少しの間飛ばして、すぐこのサーバーへ。全部失敗中なら全部試す。"""
        now = time.time()
        bad = self.__dict__.setdefault("_llm_bad", {})
        return [c for c in self.llm if bad.get(c[0], 0) <= now] or list(self.llm)

    def llm_failed(self, label: str) -> None:
        self.__dict__.setdefault("_llm_bad", {})[label] = time.time() + self.LLM_BAD_S

    def llm_ok(self, label: str) -> None:
        self.__dict__.setdefault("_llm_bad", {}).pop(label, None)

    def translate(self, msgs: list, on_delta=None) -> "tuple[str, str]":
        from tsuyaku.translate import translate_with_retry
        last = ""
        order = self.llm_order()
        for label, base, model in order:
            try:
                out = translate_with_retry(base, model, msgs, on_delta)
                if out:
                    self.last_model = model                  # 上部バーに出す(どのモデルで訳したか)
                    self.llm_ok(label)
                    return out, label
            except Exception as e:  # noqa: BLE001
                last = str(e)
            if len(order) > 1:
                self.llm_failed(label)
        print(f"[bridge] 翻訳失敗: {last}", flush=True)
        return "", ""

    def speech_pcm(self, text: str, lang: str, src_dur: float = 0.0, speed: "float | None" = None, voice: "str | None" = None) -> np.ndarray:
        """読み上げの音を 16kHz mono int16 にして返す。話速は原文を話した長さに合わせる(Sokutsu の pace)。
        日本語は機器ごとの声(voice、例 voicevox:2)で読む。指定がなければ既定の声。"""
        if lang == "ja":
            voice = voice or self.voice_ja
            vkey = voice
        else:
            vkey = (self.tts.voices or {}).get(lang, "") if self.tts is not None else ""
        ckey = (text, lang, round(self.speed_scale, 2), vkey)
        if src_dur <= 0 and speed is None and ckey in self._pcm_cache:   # 同じ定型文は作り置きを使う
            self._pcm_cache.move_to_end(ckey)
            return self._pcm_cache[ckey]
        pcm = self.speech_pcm_as(text, voice, src_dur, speed) if lang == "ja" else self._speech_pcm(text, lang, src_dur, speed)
        if src_dur <= 0 and speed is None and len(pcm):
            self._pcm_cache[ckey] = pcm
            while len(self._pcm_cache) > 48:
                self._pcm_cache.popitem(last=False)
        return pcm

    def _install_prosody(self) -> None:
        """VOICEVOX の合成を、感情の調子(話速・音高・抑揚・音量)つきにする。self.prosody が空ならいつもの声のまま。"""
        t = self.tts
        if t is None or not hasattr(t, "_voicevox_synth") or getattr(t, "_prosody_patched", False):
            return
        orig = t._voicevox_synth
        eng = self

        def synth(text, speaker, speed):
            pr = eng.prosody
            if not pr:
                return orig(text, speaker, speed)
            import requests
            from urllib.parse import quote
            base = t.voicevox_url
            r = requests.post(f"{base}/audio_query?speaker={speaker}&text={quote(text)}", headers={"Content-Type": "text/plain"}, data=b"", timeout=(1.5, 30))
            r.raise_for_status()
            q = r.json()
            q["speedScale"] = max(0.5, min(2.0, float(speed) * float(pr.get("speed", 1.0))))
            q["pitchScale"] = max(-0.15, min(0.15, float(q.get("pitchScale", 0.0)) + float(pr.get("pitch", 0.0))))
            q["intonationScale"] = max(0.0, min(2.0, float(q.get("intonationScale", 1.0)) * float(pr.get("intonation", 1.0))))
            q["volumeScale"] = max(0.3, min(1.6, float(q.get("volumeScale", 1.0)) * float(pr.get("volume", 1.0))))
            r2 = requests.post(f"{base}/synthesis?speaker={speaker}", json=q, headers={"Content-Type": "application/json"}, timeout=(1.5, 60))
            r2.raise_for_status()
            return r2.content
        t._voicevox_synth = synth
        t._prosody_patched = True

    LINE_PIN_BYTES = int(float(os.environ.get("SC_WARM_MB", "400")) * (1 << 20))      # 先読みした定型の台詞(消さない)の上限
    LINE_FREE_BYTES = 48 << 20                                                         # その場で作った台詞の作り置きの上限(古い順に捨てる)

    def _line_key(self, text: str, voice: "str | None", pros: "dict | None") -> tuple:
        return (text, voice or self.voice_ja, round(self.speed_scale, 2), tuple(sorted((pros or {}).items())))

    def line_cached(self, text: str, voice: "str | None", emotion: "dict | None" = None, raw: "dict | None" = None) -> bool:
        pros = dict(raw) if raw is not None else tuned_prosody(voice or self.voice_ja, emotion)
        return self._line_key(text, voice, pros) in self._line_cache

    def line_key_for(self, text: str, voice: "str | None", emotion: "dict | None" = None, raw: "dict | None" = None) -> tuple:
        pros = dict(raw) if raw is not None else tuned_prosody(voice or self.voice_ja, emotion)
        return self._line_key(text, voice, pros)

    def line_store(self, key: tuple, pcm: np.ndarray) -> None:
        self._line_put(key, pcm, True)

    def _line_put(self, key: tuple, pcm: np.ndarray, pin: bool) -> None:
        with self._line_lock:
            old = self._line_cache.pop(key, None)
            if old is not None:
                self._line_bytes[old[1]] -= old[0].nbytes
            self._line_cache[key] = (pcm, pin)
            self._line_bytes[pin] += pcm.nbytes
            if not pin:                                                       # その場の分だけ、古い順に捨てる(先読みの分は消さない)
                for k in list(self._line_cache):
                    if self._line_bytes[False] <= self.LINE_FREE_BYTES:
                        break
                    v = self._line_cache[k]
                    if not v[1]:
                        self._line_bytes[False] -= v[0].nbytes
                        del self._line_cache[k]

    def speech_pcm_as(self, text: str, voice: "str | None", src_dur: float = 0.0, speed: "float | None" = None, emotion: "dict | None" = None, raw: "dict | None" = None,
                      pin: bool = False) -> np.ndarray:
        """日本語を、指定した声で読む。声の切り替えは排他にし、終わったら必ず「いまの既定の声」へ戻す
        (切り替え中に管理画面で既定の声を変えても、古い声に巻き戻さない)。
        同じ文・声・調子は作り置きを使う(遊びの定型の台詞は、先に作っておく=warm.py)。pin=True は消さない作り置き。"""
        pros = dict(raw) if raw is not None else tuned_prosody(voice or self.voice_ja, emotion)
        cacheable = src_dur <= 0 and speed is None and bool(text)
        key = self._line_key(text, voice, pros) if cacheable else None
        if key is not None:
            with self._line_lock:
                hit = self._line_cache.get(key)
                if hit is not None:
                    self._line_cache.move_to_end(key)
                    if pin and not hit[1]:
                        self._line_bytes[False] -= hit[0].nbytes
                        self._line_cache[key] = (hit[0], True)
                        self._line_bytes[True] += hit[0].nbytes
                    return hit[0]
        with self._voice_lock:
            if self.tts is None:
                return self._speech_pcm(text, "ja", src_dur, speed)
            self.tts.voices["ja"] = voice or self.voice_ja
            self._install_prosody()
            self.prosody = dict(raw) if raw is not None else pros     # raw: 声の調子をそのまま使う(ものまね)
            try:
                pcm = self._speech_pcm(text, "ja", src_dur, speed)
            finally:
                self.prosody = None
                self.tts.voices["ja"] = self.voice_ja
        if key is not None and len(pcm) and (not pin or self._line_bytes[True] + pcm.nbytes <= self.LINE_PIN_BYTES):
            self._line_put(key, pcm, pin)
        return pcm

    def prewarm(self, items) -> None:
        """定型フレーズを先に声にしておく(押したらすぐ鳴る)。"""
        def run():
            for text, lang in items:
                try:
                    self.speech_pcm(text, lang)
                except Exception:  # noqa: BLE001
                    pass
        threading.Thread(target=run, daemon=True).start()

    def _speech_pcm(self, text: str, lang: str, src_dur: float = 0.0, speed: "float | None" = None) -> np.ndarray:
        from tsuyaku.pace import engine_of
        wav = None
        speed = None
        if self.tts is not None:
            spec = (self.tts.voices or {}).get(lang, "")
            eng = engine_of(spec)
            base = 1.0
            if speed is None:
                speed = self.pace.choose(text, eng, lang, src_dur, 0.0, base=base) if src_dur > 0 else base
            speed = max(0.9, speed)                       # 日本語が速すぎると言われたので、遅い側へは下げ過ぎない
            speed = max(0.6, min(1.8, speed * self.speed_scale))
            try:
                wav = self.tts._synth(text, lang, speed)
            except Exception as e:  # noqa: BLE001
                print(f"[bridge] Kokoro 失敗→say: {e}", flush=True)
        if wav is not None:
            pcm = _wav_bytes_to_pcm16k(wav)
            if self.tts is not None and speed is not None:
                self.pace.observe(text, engine_of((self.tts.voices or {}).get(lang, "")), lang, speed, len(pcm) / SR)
            self.last_speed = speed
            return pcm
        return self._say(text, lang)

    def _say(self, text: str, lang: str) -> np.ndarray:
        if shutil.which("say") is None:                     # Linux には macOS の say が無い → 音は出さず字幕だけ
            if not getattr(self, "_nosay_noted", False):
                print(f"[bridge] {lang} の声がこの環境にありません(字幕のみ)", flush=True)
                self._nosay_noted = True
            return np.zeros(0, np.int16)
        with self._say_lock, tempfile.TemporaryDirectory() as d:
            a, w = os.path.join(d, "s.aiff"), os.path.join(d, "s.wav")
            voice = (getattr(self.s, "tts_voice", {}) or {}).get(lang, "")
            name = voice.split(":", 1)[1] if voice.startswith("say:") else SAY_VOICE.get(lang, "Kyoko")
            subprocess.run(["say", "-v", name, "-o", a, "--", text], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", a, w],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return _wav_bytes_to_pcm16k(open(w, "rb").read())


_HALLU_FRAG = re.compile(
    r"(ご視聴(いただき)?(ありがとう|あり)[^。！？!?]*|ご清聴(ありがとう)[^。！？!?]*|チャンネル登録[^。！？!?]*|字幕[^。！？!?]*(提供|作成|翻訳)[^。！？!?]*|"
    r"请不吝点赞.*|点赞.{0,6}订阅.*|订阅.{0,4}转发.*|感谢观看.*|谢谢观看.*|明镜与点点.*|字幕由.*|"
    r"시청해 주셔서.*|구독.{0,6}좋아요.*|"
    r"thank(s| you)( so much)? for watching.*|please subscribe.*|subscribe to (my|our|the) channel.*|like and subscribe.*)\s*$", re.I)
_REPEAT = re.compile(r"(.{2,8}?)\1{3,}")


def clean_hallucination(text: str, dur: float = 0.0) -> str:
    """雑音から出る定番の幻聴を取り除いた文を返す(全部が幻聴なら空文字)。"""
    t = (text or "").strip()
    if not t:
        return ""
    u = _HALLU_FRAG.sub("", t)
    if u != t:                                                       # 幻聴を削ったときだけ、残りの句読点を整える
        t = u.strip(" 、,，。.!！?？\n")
    if _REPEAT.search(t) and len(_REPEAT.sub("", t).strip()) <= 2:   # 同じ短い音の繰り返しだけ(例: ヨドッグ×4)
        return ""
    if len(t) <= 1 and dur < 1.0 and not t.isdigit():                # 1文字だけの短い音(ん / あ)
        return ""
    return t


def short_model(name: str) -> str:
    """上部バー用にモデル名を短くする(例: qwen3-4b-instruct → Qwen3-4B / google/gemma-3-4b → Gemma-3-4B)。"""
    n = (name or "").split("/")[-1]
    n = re.sub(r"[-_.](instruct|it|chat|gguf|q\d.*|\d{4})$", "", n, flags=re.I)
    n = re.sub(r"[-_.](instruct|it|chat|gguf|q\d.*|\d{4})$", "", n, flags=re.I)
    n = re.sub(r"(\d)b\b", r"\1B", n, flags=re.I)
    return (n[:1].upper() + n[1:])[:15]


def _wav_bytes_to_pcm16k(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data), "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != SR and len(x):
        n = int(len(x) * SR / sr)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return np.clip(x, -32768, 32767).astype("<i2")


# ---------------------------------------------------------------- 顔検出(任意: cv2 があれば)
class Face:
    """顔検出。OpenCV 公式の YuNet(bridge/models)を使い、無ければ Haar に落ちる。"""

    YUNET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "face_detection_yunet_2023mar.onnx")
    SFACE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "face_recognition_sface_2021dec.onnx")

    def __init__(self):
        self.cv2 = None
        self.yunet = None
        self.det = None
        self.sface = None
        try:
            import cv2
            if os.path.exists(self.YUNET) and hasattr(cv2, "FaceDetectorYN"):
                self.yunet = cv2.FaceDetectorYN.create(self.YUNET, "", (320, 240), 0.6, 0.3, 50)
            if self.yunet is not None and os.path.exists(self.SFACE) and hasattr(cv2, "FaceRecognizerSF"):
                try:
                    self.sface = cv2.FaceRecognizerSF.create(self.SFACE, "")
                except Exception as e:  # noqa: BLE001 - 識別だけ使えなくても追従は動かす
                    print(f"[bridge] 顔の識別モデルを読めません: {e}", flush=True)
            det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml") if hasattr(cv2, "CascadeClassifier") else None
            if det is not None and not det.empty():
                self.det = det
            if self.yunet is None and self.det is None:
                raise RuntimeError("no detector")
            self.cv2 = cv2
            print(f"[bridge] 顔検出: {'YuNet' if self.yunet is not None else 'Haar'}  顔の識別: {'SFace' if self.sface is not None else 'なし'}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] 顔追従なし({e})。pip install --no-deps opencv-python-headless==4.10.0.84", flush=True)

    def decode(self, jpeg: bytes):
        if self.cv2 is None:
            return None
        return self.cv2.imdecode(np.frombuffer(jpeg, np.uint8), self.cv2.IMREAD_COLOR)

    def find(self, img):
        """最も大きい顔 (x, y, w, h, row)。row は YuNet の生の行(識別に使う。Haar のときは None)。"""
        if self.cv2 is None or img is None:
            return None
        cv2 = self.cv2
        h, w = img.shape[:2]
        if self.yunet is not None:
            self.yunet.setInputSize((w, h))
            _, f = self.yunet.detect(img)
            if f is None or len(f) == 0:
                return None
            row = max(f, key=lambda r: r[2] * r[3])
            return (float(row[0]), float(row[1]), float(row[2]), float(row[3]), row)
        g = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        r = self.det.detectMultiScale(g, 1.1, 3, minSize=(max(28, w // 10),) * 2)
        if len(r) == 0:
            return None
        x, y, fw, fh = max(r, key=lambda b: b[2] * b[3])
        return (float(x), float(y), float(fw), float(fh), None)

    @staticmethod
    def rel(f, shape) -> "tuple[float, float, float] | None":
        """find の結果を、画面中心からのずれ(ex,ey ∈ [-1,1])と大きさ比にする。"""
        if f is None:
            return None
        h, w = shape[:2]
        x, y, fw, fh = f[:4]
        return (float((x + fw / 2) / w * 2 - 1), float((y + fh / 2) / h * 2 - 1), float(fw / w))

    def detect(self, img) -> "tuple[float, float, float] | None":
        """最も大きい顔の中心を画面中心からのずれ(ex,ey ∈ [-1,1])と大きさ比で返す。"""
        return self.rel(self.find(img), img.shape) if img is not None else None

    def embed(self, img, f):
        """顔の特徴(128 次元)。識別モデルが無い/Haar のときは None。"""
        if self.sface is None or f is None or f[4] is None:
            return None
        try:
            return self.sface.feature(self.sface.alignCrop(img, f[4])).flatten().astype(np.float32)
        except Exception:  # noqa: BLE001
            return None

    def locate(self, jpeg: bytes):
        return self.detect(self.decode(jpeg))


# ---------------------------------------------------------------- 接続ごとの会話処理
class Session:
    voice = None                                              # この機器の日本語の声(台帳の値。無ければ既定の声)

    def __init__(self, eng: Engine, send, pair=("ja", "en"), topic: str = ""):
        """send(dict | bytes): スレッドから呼んでよい(接続へ送る)。"""
        from tsuyaku.audio import AutoGain, StreamSegmenter
        from tsuyaku.segmenter import Endpointer

        self.eng, self.send = eng, send
        self.pair, self.topic = tuple(getattr(eng, "pair_override", None) or pair), getattr(eng, "topic_override", None) or topic
        s = eng.s
        fine = eng.remote_ear
        sil = 0.2 if fine else s.min_silence_s
        eng.vad_conf.silero_vad.min_silence_duration = sil
        eng.vad_conf.silero_vad.max_speech_duration = 30.0
        self.vad = eng.sherpa.VoiceActivityDetector(eng.vad_conf, buffer_size_in_seconds=60)
        self.sg = StreamSegmenter(SR, min_speech_s=s.min_speech_s, min_silence_s=sil,
                                  cut_s=0.18 if fine else 1e9, max_s=s.max_speech_s)
        self.agc = AutoGain()
        self.ep = Endpointer(rejoin=(lambda a: self._stt(a)) if fine else None,
                             hold_open_s=s.hold_open_s)
        self.history: list = []
        self.buf = np.zeros(0, np.float32)
        self.t0 = time.time()
        self.muted_until = 0.0
        self.segs: "queue.Queue" = queue.Queue()
        self.alive = True
        self.talking = False
        self.batt = self.dbg = self.vol = None
        self._tx_lock = threading.Lock()
        self._rms_t, self._rms_n, self._rms_s, self._rms_pk = time.time(), 0, 0.0, 0.0
        self._amb = 0.01
        self.dev_id, self.name, self.ip, self.fw = "", "StackChan", "", ""      # identify() で台帳から決まる
        self.lang_pref = None
        threading.Thread(target=self._worker, daemon=True).start()
        admin.HUB.add_session(self)

    # --- 機器の識別と自動セットアップ(複数台対応)
    def identify(self, j: dict, ip: str) -> None:
        """hello から機器を特定し、台帳に記録する。初めての機器なら自動セットアップする。"""
        dev_id = str(j.get("id") or f"ip-{ip}")
        rec, new = admin.REG.touch(dev_id, ip, str(j.get("fw", "")))
        self.dev_id, self.name, self.ip, self.fw = dev_id, rec["name"], ip, rec.get("fw", "")
        admin.HUB.replace_dups(self)                       # 同じ機器の古い接続が残っていれば閉じる
        self.lang_pref = rec.get("lang")
        if self.lang_pref in ("en", "zh", "ko"):
            self.pair = ("ja", self.lang_pref)
        self.topic = rec.get("topic", "") or ""
        self.voice = rec.get("voice") or None                 # この機器の日本語の声(無ければ既定)
        self.send({"t": "name", "name": self.name})            # 画面の右下に機器名を出す
        self.send({"t": "faceStyle", "id": self._face_style(rec)})   # 顔の種類(キャラごとに違う顔)
        if new:
            self.auto_setup(rec)
        else:
            print(f"[bridge] 機器: {self.name} ({dev_id[-6:]}) {ip}", flush=True)
            admin.HUB.event(f"{self.name} が接続しました")

    def auto_setup(self, rec: dict) -> None:
        """新しい機器の初期設定: 言語・音量・明るさ・表情を整え、画面に名前を出す。顔追従の校正は最初の画像で自動で始まる。"""
        default_lang = (getattr(self.eng, "pair_override", None) or ("ja", "en"))[1]
        admin.REG.set(self.dev_id, lang=default_lang, setup=time.time(), vol=150)
        self.lang_pref = default_lang
        self.pair = ("ja", default_lang)
        print(f"[bridge] 新しい機器を自動セットアップ: {self.name} ({self.dev_id[-6:]}) {self.ip}", flush=True)
        admin.HUB.event(f"新しい機器を自動セットアップ: {self.name}")
        self.send({"t": "volume", "v": 150})
        self.send({"t": "bright", "v": 140})
        self.send({"t": "face", "state": "happy"})
        self.send({"t": "servo", "pan": 0, "tilt": 200, "speed": 300})
        self.send({"t": "caption", "lang": "ja", "to": "ja", "orig": f"セットアップ完了 {self.name}", "trans": "", "final": True})

    def _face_style(self, rec: dict) -> int:
        """この機器の顔の種類。管理画面で選んでいればそれ、なければ登録順のキャラに合わせた既定の顔。"""
        f = rec.get("face")
        if isinstance(f, int) and 0 <= f < len(_devices.FACE_STYLES):
            return f
        try:
            return _devices.DEFAULT_FACE[admin.HUB.discuss.persona_of(self.dev_id) % len(_devices.DEFAULT_FACE)]
        except Exception:  # noqa: BLE001
            return 0

    def _stage(self, name: str, state: str = "done", **info) -> None:
        admin.HUB.stage(name, state, dev=self.name, **info)

    def _stt(self, a):
        """この機器の言語ペアで聞き取る(複数台で言語が違っても混ざらないよう、設定と呼び出しをまとめて行う)。"""
        e = self.eng
        with e.stt_lock:
            if e.remote_ear:
                e.stt.langs = self.pair
            return e.stt.transcribe(a)

    # --- 端末から来た声(任意の長さ)を 512 サンプルずつ VAD へ
    def feed_seq(self, body: bytes) -> None:
        """連番つき音声 [seq u16 LE][PCM]。欠け・到着間隔を数えて、10秒ごとに表示する(音声欠けの調査)。"""
        if len(body) < 2:
            return
        seq = body[0] | (body[1] << 8)
        now = time.time()
        st = self.__dict__.setdefault("_aud", {"last": None, "n": 0, "lost": 0, "gapmax": 0.0, "t": now, "late": 0, "tl": now})
        if st["last"] is not None:
            d = (seq - st["last"]) & 0xFFFF
            if 1 < d < 1000:
                st["lost"] += d - 1
            dt = now - st["tl"]
            st["gapmax"] = max(st["gapmax"], dt)
            if dt > 0.2:
                st["late"] += 1
        st["last"], st["tl"] = seq, now
        st["n"] += 1
        if now - st["t"] > 10.0:
            exp = (now - st["t"]) * SR / 512
            print(f"[bridge] 音声受信 {st['n']}/{exp:.0f}フレーム 欠け{st['lost']} 最大間隔{st['gapmax']*1000:.0f}ms 200ms超{st['late']}回", flush=True)
            st.update(t=now, n=0, lost=0, gapmax=0.0, late=0)
        self.feed_pcm(body[2:])

    def feed_pcm(self, pcm: bytes) -> None:
        x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
        if os.environ.get("SC_DUMP"):                      # 聞き取りの調査用: 受けた声をそのまま保存
            with open(os.environ["SC_DUMP"], "ab") as f:
                f.write(pcm[: len(pcm) // 2 * 2])
        self._rms_n += len(x)
        admin.HUB.keep_raw(pcm[: len(pcm) // 2 * 2], self.dev_id)
        admin.HUB.mic(float(np.sqrt(np.mean(x * x))) if len(x) else 0.0, self.dev_id)
        if not admin.HUB.listen or admin.HUB.discussing():   # 「聞かない」のとき・3 人会話の間(お互いの声を拾わない)
            return
        self._rms_s += float(np.sum(x * x))
        self._rms_pk = max(self._rms_pk, float(np.max(np.abs(x))) if len(x) else 0.0)
        if time.time() - self._rms_t > 5.0:               # マイクが生きているか5秒ごとに表示
            r = (self._rms_s / max(1, self._rms_n)) ** 0.5
            print(f"[bridge] マイク入力 rms={r:.4f} peak={self._rms_pk:.3f} ({self._rms_n/SR:.1f}秒ぶん)", flush=True)
            self._rms_t, self._rms_n, self._rms_s, self._rms_pk = time.time(), 0, 0.0, 0.0
        self.buf = np.concatenate([self.buf, x])
        while len(self.buf) >= 512:
            ch, self.buf = self.buf[:512], self.buf[512:]
            if time.time() < self.muted_until:      # 自分の声の読み上げ中は聞かない(半二重)
                ch = np.zeros(512, np.float32)
            raw = float(np.sqrt(np.mean(ch * ch)))
            if time.time() >= self.muted_until:             # 部屋の雑音の大きさを常に測る(静かな側へ素早く、大きい側へゆっくり)
                self._amb = (0.9 * self._amb + 0.1 * raw) if raw < self._amb else min(0.03, 0.999 * self._amb + 0.001 * raw)
                admin.HUB.gs(self.dev_id)["amb"] = self._amb
            ch = self.agc(ch)
            self.vad.accept_waveform(ch)
            while not self.vad.empty():
                self.vad.pop()
            _sp = self.vad.is_speech_detected()
            if _sp and not self.talking and raw < max(admin.HUB.gate_min, self._amb * admin.HUB.gate_mult):
                _sp = False                                  # 雑音と同じ大きさの音では話し始めと見なさない(話の途中は続ける)
                admin.HUB.gs(self.dev_id)["gated"] += 1
            elif _sp:
                _g = admin.HUB.gs(self.dev_id)
                _g["speech"] = 0.95 * _g["speech"] + 0.05 * raw
            if _sp:
                admin.HUB.vad(self.dev_id)
            for seg, lo, hi, pre in self.sg.feed(ch, _sp):
                self.segs.put((seg, lo, hi, pre))
            talking = self.sg.speaking_since() is not None
            if talking != self.talking:                  # 話し始め/終わりの変化だけ顔へ伝える
                self.talking = talking
                if talking:
                    self.send({"t": "face", "state": "listening"})

    # ---------------- 顔追従(カメラ画像は端末から約4枚/秒) ----------------
    CALIB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calib.json")

    def _load_calib(self):
        try:
            with open(self.CALIB_FILE) as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return None

    def _people_step(self, img, f, now: float, st: dict) -> None:
        """見えている顔が誰かを当てる/登録の撮影/手を振りの検出。毎フレーム呼ばれる(重い処理は間引く)。"""
        import people as pp
        H = admin.HUB
        cv2 = self.eng.face.cv2
        P = st.setdefault("pp", {"vote": pp.Voter(), "wave": pp.WaveDetector(), "last_id": 0.0, "name": "", "ann": {}, "gone": 0.0})
        if f is None:
            if now - P["gone"] > 3.0 and P["name"]:
                P["name"] = ""
                P["vote"].reset()
                H.set_now(self.dev_id, {"name": "", "score": 0.0, "t": now, "face": False})
            P["gone"] = P["gone"] or now
            P["wave"].reset()
            if H.reg and H.reg.get("dev") in ("", self.dev_id) and now - H.reg["t0"] > 25:
                H.reg_end("顔が見つかりませんでした。カメラの前で、もう一度試してください")
            return
        P["gone"] = 0.0
        h, w = img.shape[:2]
        big = f[2] / w >= 0.12                                    # 小さすぎる顔(遠い・横向き)は識別しない
        sc = float(f[4][14]) if f[4] is not None else 0.0
        reg = H.reg if (H.reg and H.reg.get("dev") in ("", self.dev_id)) else None
        if reg and big and sc >= 0.85 and now - reg.get("last", 0) >= 0.5:
            emb = self.eng.face.embed(img, f)
            if emb is not None:
                reg["last"] = now
                reg["got"].append(emb)
                if len(reg["got"]) >= reg["need"]:
                    H.reg_done()
        elif H.people.db and big and now - P["last_id"] >= 1.0:
            P["last_id"] = now
            emb = self.eng.face.embed(img, f)
            if emb is not None:
                nm, score = H.people.identify(emb)
                who = P["vote"].add(nm)
                H.set_now(self.dev_id, {"name": who, "score": round(score, 2), "t": now, "face": True})
                if who and who != P["name"] and now - P["ann"].get(who, 0.0) > 20.0:
                    P["ann"][who] = now
                    H.event(f"{self.name}: {who}さんを見つけました")
                    self.send({"t": "caption", "lang": "ja", "to": "ja", "orig": f"{who}さん", "trans": "", "final": True})
                P["name"] = who
        elif not H.people.db:
            H.set_now(self.dev_id, {"name": "", "score": 0.0, "t": now, "face": True})
        if now < st.get("settle", 0.0) + 0.6:                     # 首が動いた直後は画像が全面で変わるので見ない
            P["wave"].reset()
            return
        small = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (pp.WaveDetector.W, pp.WaveDetector.H))
        kx, ky = pp.WaveDetector.W / w, pp.WaveDetector.H / h
        box = (f[0] * kx, f[1] * ky, (f[0] + f[2]) * kx, (f[1] + f[3]) * ky)
        if P["wave"].update(small, box, now) and H.wave_on:
            self._on_wave(P["name"], st, now)

    def _on_wave(self, name: str, st: dict, now: float) -> None:
        H = admin.HUB
        if getattr(H, "edu_on", False):                           # 教材モード: 手振りには反応しない(声も動きも出さない。遊びを選ぶまで話さない)
            return
        H.wave_count += 1
        H.wave_t = now
        H.event(f"{self.name}: 手を振りました" + (f"({name}さん)" if name else ""))
        print(f"[bridge] 手を振りを検出{'(' + name + ')' if name else ''}", flush=True)
        self.send({"t": "face", "state": "happy"})
        self.send({"t": "servo", "dtilt": -90, "speed": 400})
        threading.Timer(0.45, lambda: self.send({"t": "servo", "dtilt": 90, "speed": 400})).start()
        st["settle"] = now + 1.6                                  # うなずいた首の動きで追従が暴れないように
        if H.wave_voice:
            self.say(f"こんにちは、{name}さん" if name else "こんにちは", "ja")

    def on_frame(self, jpeg: bytes) -> None:
        self.last_jpeg, self.last_jpeg_ts = jpeg, time.time()    # 管理画面の「手の確認」が見る最新のカメラ画像
        cb = self.__dict__.get("hand_cb")                       # じゃんけん中: 画像を手の認識に回す(この間、顔追従の首は止める)
        if cb is not None:
            try:
                cb(jpeg)
            except Exception:  # noqa: BLE001
                pass
            return
        face = self.eng.face
        snap = os.environ.get("SC_SNAP_DIR")                  # 調査用: 最新のカメラ画像を保存(ローカルのみ)
        if snap:
            try:
                with open(os.path.join(snap, "latest.jpg"), "wb") as f:
                    f.write(jpeg)
            except Exception:  # noqa: BLE001
                pass
        if os.environ.get("SC_NO_TRACK") or not admin.HUB.track:                       # 試験用: 顔追従と画面の自動調整を止める
            return
        img = face.decode(jpeg)
        if img is None:
            return
        now = time.time()
        self._frame, self._frame_t = img, now
        self._auto_bright(img, now)
        st = self.__dict__.setdefault("_trk", {"calib": self._load_calib(), "busy": False, "seen": 0.0,
                                               "react": 0.0, "settle": 0.0, "n": 0, "hit": 0, "t": now})
        st["n"] += 1
        if now - st["t"] > 10.0:
            print(f"[bridge] カメラ {st['n']}枚/10秒 顔検出{st['hit']}回 校正{'済' if st['calib'] else '未'}", flush=True)
            st.update(n=0, hit=0, t=now)
        if st["busy"]:
            return
        if st["calib"] is None and not st.get("tried"):
            st["tried"] = True
            threading.Thread(target=self.calibrate, daemon=True).start()
            return
        f = face.find(img)
        r = face.rel(f, img.shape)
        try:
            self._people_step(img, f, now, st)
        except Exception as e:  # noqa: BLE001 - 識別や手振りの不具合で追従を止めない
            if not st.get("pp_err"):
                st["pp_err"] = True
                print(f"[bridge] 顔の識別/手振りの処理でエラー: {type(e).__name__}: {e}", flush=True)
        if r is None:
            return
        st["hit"] += 1
        ex, ey, size = r
        if now - st["seen"] > 8.0 and now - st["react"] > 30.0:      # 久しぶりに人を見つけた: 喜ぶ
            st["react"] = now
            self.send({"t": "face", "state": "happy"})
        st["seen"] = now
        if now - st.get("look_t", 0.0) > 0.5:                          # 目線だけは常に顔へ(首が動かない範囲でも)
            st["look_t"] = now
            self.send({"t": "look", "ex": round(ex, 2), "ey": round(ey, 2), "size": round(size, 2)})
        cal = st["calib"]
        if cal is None or now < st["settle"]:
            return
        if abs(ex) < 0.12 and abs(ey) < 0.14:                          # 中央付近なら動かさない
            return
        h, w = img.shape[:2]
        dpan = -ex * (w / 2) / cal["pan_px"] * 0.7                      # 0.6 = 一度に詰める割合(行き過ぎ防止)
        dtilt = -ey * (h / 2) / cal["tilt_px"] * 0.7 if abs(ey) >= 0.14 else 0.0
        dpan = max(-350.0, min(350.0, dpan))
        dtilt = max(-250.0, min(250.0, dtilt))
        st["settle"] = now + 0.7                                        # 首が動いて新しい画像になるまで待つ
        st["cmds"] = st.get("cmds", 0) + 1
        if st["cmds"] % 5 == 1:
            print(f"[bridge] 追従: 顔のずれ ex={ex:+.2f} ey={ey:+.2f} → 首 pan{dpan:+.0f} tilt{dtilt:+.0f}", flush=True)
        self.send({"t": "servo", "dpan": int(dpan), "dtilt": int(dtilt), "speed": 350})

    def _auto_bright(self, img, now: float) -> None:
        """カメラ画像の平均輝度から部屋の明るさを推定し、暗い部屋では端末の画面を暗くする(夜に眩しくない)。"""
        lum = float(img.mean()) if img is not None else 0.0
        ema = getattr(self, "_lum", None)
        self._lum = lum if ema is None else ema * 0.9 + lum * 0.1
        if now - getattr(self, "_bright_t", 0.0) < 10.0:
            return
        self._bright_t = now
        target = 35 if self._lum < 25 else (80 if self._lum < 55 else 140)
        if target != getattr(self, "_bright", None):
            self._bright = target
            print(f"[bridge] 部屋の明るさ {self._lum:.0f}/255 → 画面の明るさ {target}", flush=True)
            self.send({"t": "bright", "v": target})

    def calibrate(self) -> None:
        """首を既知の量だけ動かし、画像の動きから「1単位あたり何ピクセル動くか(向きつき)」を測る。"""
        st = self._trk
        cv2 = self.eng.face.cv2
        st["busy"] = True
        try:
            def settle_frame(wait: float):
                time.sleep(wait)
                t = self._frame_t
                for _ in range(40):                                  # 新しい画像が来るまで
                    if self._frame_t > t:
                        break
                    time.sleep(0.05)
                time.sleep(0.4)
                return cv2.cvtColor(self._frame, cv2.COLOR_BGR2GRAY).astype(np.float32)

            def shift(a, b):
                win = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
                (dx, dy), resp = cv2.phaseCorrelate(a, b, win)
                return dx, dy, resp

            print("[bridge] 顔追従の校正を始めます(首を少し動かします)", flush=True)
            self.send({"t": "servo", "pan": -100, "tilt": 250, "speed": 300})
            A = settle_frame(2.2)
            self.send({"t": "servo", "pan": 100, "tilt": 250, "speed": 300})
            B = settle_frame(2.2)
            dx, _dy, r1 = shift(A, B)
            self.send({"t": "servo", "pan": 0, "tilt": 200, "speed": 300})
            A2 = settle_frame(2.2)
            self.send({"t": "servo", "pan": 0, "tilt": 320, "speed": 300})
            B2 = settle_frame(2.2)
            _dx2, dy, r2 = shift(A2, B2)
            self.send({"t": "servo", "pan": 0, "tilt": 250, "speed": 300})
            pan_px, tilt_px = dx / 200.0, dy / 120.0
            print(f"[bridge] 校正の測定: 首+200で横{dx:.1f}px(信頼度{r1:.2f}) / 首+120で縦{dy:.1f}px(信頼度{r2:.2f})", flush=True)
            if abs(dx) < 6 or abs(dy) < 4 or r1 < 0.03 or r2 < 0.03:
                print("[bridge] 校正に失敗しました(背景の模様が少ない・首が動いていない可能性)。顔追従は行いません。"
                      "再試行は calib.json を消して再起動", flush=True)
                st["calib"] = None
                st["tried"] = True
            else:
                st["calib"] = {"pan_px": pan_px, "tilt_px": tilt_px}
                with open(self.CALIB_FILE, "w") as f:
                    json.dump(st["calib"], f)
                print(f"[bridge] 校正完了: pan {pan_px:+.3f}px/単位, tilt {tilt_px:+.3f}px/単位", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] 校正エラー: {type(e).__name__}: {e}", flush=True)
        finally:
            st["busy"] = False
            st["settle"] = time.time() + 1.0

    def device_event(self, j: dict) -> None:
        """端末からの電池・イベント通知。電池が少なければ一度だけ声でも知らせる。"""
        t = j.get("t")
        if t == "vol":
            self.vol = j.get("v")
            return
        if t == "dbg":
            if "rssi" in j:
                self.dbg = j
            return
        if t == "batt":
            self.batt = j
            if j.get("vol") is not None:
                self.vol = j["vol"]
            low = j.get("pct", 100) < 15 and not j.get("chg")
            if low and time.time() - getattr(self, "_batt_warn", 0.0) > 900:
                self._batt_warn = time.time()
                if getattr(admin.HUB, "edu_on", False):                 # 教材モード: 声は出さない(管理画面の準備チェックと記録で知らせる)
                    admin.HUB.event(f"電池が少ないです: {self.dev_id} ({j.get('pct')}%)")
                else:
                    self.say("電池が少なくなってきました。充電してください。", "ja" if "ja" in self.pair else self.pair[0])
            if time.time() - getattr(self, "_batt_log", 0.0) > 300:
                self._batt_log = time.time()
                print(f"[bridge] 電池 {j.get('v')}V ({j.get('pct')}%{' 充電中' if j.get('chg') else ''})", flush=True)
        elif t == "event":
            print(f"[bridge] 端末イベント: {j}", flush=True)
            if j.get("name") == "touch":
                self.on_pat()

    PAT_LINES = {                                   # 頭を撫でられたときの一言(キャラごと)。子どもにやさしい言葉だけ
        0: ["えへへ、くすぐったいのだ", "なでなで、うれしいのだ", "もっとなでてほしいのだ"],
        1: ["あら、ありがとう。うれしいわ", "ふふ、くすぐったいわ", "なでてくれて、ありがとう"],
        2: ["えへへ、うれしいな", "わあ、なでなでだ！", "くすぐったいよ〜"],
    }

    def on_pat(self) -> None:
        """頭を撫でられた: 話し中・しりとり中・会話中でなければ、キャラの声で一言返す(10秒に1回まで)。"""
        now = time.time()
        if now - getattr(self, "_pat_at", 0.0) < 10.0 or "ja" not in self.pair:
            return
        g = getattr(admin.HUB, "game", None)
        if (g is not None and g.on) or admin.HUB.discussing() or getattr(admin.HUB, "edu_on", False) or getattr(admin.HUB, "lesson", None) is not None and any(h.own_on for h in admin.HUB.lesson.hubs()):
            return                                      # 教材モード・遊びの最中は、何も話さない
        self._pat_at = now
        try:
            k = admin.HUB.discuss.persona_of(self.dev_id) % 3
        except Exception:  # noqa: BLE001
            k = 0
        ls = self.PAT_LINES[k]
        self._pat_n = getattr(self, "_pat_n", 0) + 1
        threading.Thread(target=lambda: self.say(ls[self._pat_n % len(ls)], "ja", "happy"), daemon=True).start()

    def resend(self, seq: int) -> None:
        """端末が「欠けた」と知らせた音声を、1回だけ送り直す。"""
        item = getattr(self, "_sent", {}).pop(seq, None)
        if item:
            print(f"[bridge] 音声が欠けたので再送します(seq {seq})", flush=True)
            threading.Thread(target=lambda: self._send_pcm(item[0], _resend=True), daemon=True).start()

    def close(self) -> None:
        self.alive = False
        self.segs.put(None)

    def hello(self) -> None:
        """端末の挨拶(hello)を受けたとき。久しぶり(起動直後)なら自己紹介を一度話す。"""
        now = time.time()
        last = getattr(self.eng, "last_hello", 0.0)         # USB と Wi-Fi の両方から hello が来ても、あいさつは1回だけ
        # あいさつは話さない(要望により廃止)。環境変数 SC_GREET_BOOT=1 で起動時に一度だけ話す
        if now - last > 20.0 and "ja" in self.pair and os.environ.get("SC_GREET_BOOT") == "1":
            self.say("こんにちは。スタックチャンです。", "ja")
        self.eng.last_hello = now
        self.send_status()

    _VOL_UP = re.compile(r"(音量|ボリューム|音).{0,6}(上げ|あげ|大き|おおき)|(もっと|少し|すこし).{0,4}大きい?(声|音)|volume up|louder|turn (it )?up", re.I)
    _VOL_DN = re.compile(r"(音量|ボリューム|音).{0,6}(下げ|さげ|小さ|ちいさ)|(もっと|少し|すこし).{0,4}小さい?(声|音)|うるさい|volume down|quieter|turn (it )?down|too loud", re.I)
    _VOL_MAX = re.compile(r"(音量|ボリューム).{0,4}(最大|マックス)|max volume|full volume", re.I)
    _VOL_MIN = re.compile(r"(音量|ボリューム).{0,4}(最小|ゼロ|ミュート)|mute|minimum volume", re.I)

    _LANG_CMD = re.compile(r"(英語|中国語|韓国語|English|Chinese|Korean).{0,6}(モード|にして|に切り替え|に変更|に変えて|mode)|(switch|change) to (English|Chinese|Korean)", re.I)
    _LANG_OF = (("英語", "en"), ("english", "en"), ("中国語", "zh"), ("chinese", "zh"), ("韓国語", "ko"), ("korean", "ko"))

    def apply_pair(self, pair) -> None:
        """端末の hello/config の言語ペア。声で切り替えた言語(pair_override)があればそちらを優先。"""
        if self.lang_pref in ("en", "zh", "ko"):                    # この機器で選んだ言語(台帳)を優先
            self.pair = ("ja", self.lang_pref)
        else:
            self.pair = tuple(getattr(self.eng, "pair_override", None) or pair)

    def set_pair_b(self, code: str) -> None:
        """通訳する相手の言語を切り替える(日本語はそのまま。声コマンド「中国語モード」など)。"""
        self.pair = ("ja", code)
        self.lang_pref = code
        self.eng.pair_override = self.pair                   # 新しい機器の初期値にもなる
        if self.dev_id:
            admin.REG.set(self.dev_id, lang=code)            # この機器は再起動しても戻らない
        self.history = []
        names = {"en": "英語", "zh": "中国語", "ko": "韓国語"}
        print(f"[bridge] 言語ペア({self.name}): ja↔{code}", flush=True)
        admin.HUB.save()
        self.send({"t": "caption", "lang": "ja", "to": code, "orig": f"{names.get(code, code)}モード", "trans": "", "final": True})

    _TOPIC_CMD = re.compile(r"^(?:スタックチャン[、,]?\s*)?(?:話題|トピック|テーマ)(?:は|を)?\s*(.{1,20}?)\s*(?:です|にして|にする|について)?[。.]?$")
    _TOPIC_CLEAR = re.compile(r"(話題|トピック|テーマ).{0,3}(リセット|クリア|なし|消して)")

    def _voice_cmd(self, text: str) -> bool:
        """短い操作コマンドだけ拾う(長い文は通訳対象。誤爆を避けるため 28 文字まで)。"""
        t = text.strip()
        if len(t) > 28:
            return False
        if self._TOPIC_CLEAR.search(t):
            self.topic = ""
            if self.dev_id:
                admin.REG.set(self.dev_id, topic="")
            self.history = []
            print("[bridge] 話題をリセット", flush=True)
            self.send({"t": "caption", "lang": "ja", "orig": "話題をリセットしました", "trans": "", "final": True})
            return True
        m = self._TOPIC_CMD.match(t)
        if m and m.group(1):
            self.topic = m.group(1)                              # この機器の台帳に保存(再接続しても保持)
            if self.dev_id:
                admin.REG.set(self.dev_id, topic=self.topic)
            self.history = []
            print(f"[bridge] 話題: {self.topic}", flush=True)
            self.send({"t": "caption", "lang": "ja", "orig": f"話題: {self.topic}", "trans": "", "final": True})
            return True
        if self._LANG_CMD.search(t):
            low = t.lower()
            for key, code in self._LANG_OF:
                if key in low:
                    self.set_pair_b(code)
                    return True
        for rx, msg in ((self._VOL_MAX, {"v": 255}), (self._VOL_MIN, {"v": 20}),
                        (self._VOL_UP, {"delta": 40}), (self._VOL_DN, {"delta": -40})):
            if rx.search(t):
                print(f"[bridge] 音声コマンド(音量): {t}", flush=True)
                self.send({"t": "volume", **msg})
                return True
        return False

    CTRL = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ctl")

    def _poll_control(self) -> None:
        """ローカルの制御ファイル(bridge/.ctl/<名前>)で操作する(開発用)。shot=端末の画面を撮る。"""
        try:
            for name, msg in (("volume_up", {"delta": 40}), ("volume_down", {"delta": -40})):
                f = os.path.join(self.CTRL, name)
                if os.path.exists(f):
                    os.remove(f)
                    self.send({"t": "volume", **msg})
            f = os.path.join(self.CTRL, "say.txt")              # 1行目=言語コード、2行目以降=読む文章(開発・試験用)
            if os.path.exists(f):
                lines = open(f, encoding="utf-8").read().splitlines()
                os.remove(f)
                if len(lines) >= 2:
                    self.say("\n".join(lines[1:]), lines[0].strip() or "ja")
            if os.path.exists(os.path.join(self.CTRL, "shot")):
                os.remove(os.path.join(self.CTRL, "shot"))
                self.send({"t": "shot"})
        except OSError:
            pass

    def send_status(self) -> None:
        """上部バー用: 時刻と、いま処理している機器(耳=聞き取り / 訳)を端末へ知らせる。"""
        self._status_t = time.time()
        ear = getattr(self.eng.stt, "last_where", "") or ("s109" if self.eng.remote_ear else "mac")
        llm = getattr(self.eng, "last_llm", "") or (self.eng.llm[0][0] if self.eng.llm else "")
        lt = time.localtime()
        self.send({"t": "status", "h": lt.tm_hour, "m": lt.tm_min, "s": lt.tm_sec,
                   "ear": "s109" if "s109" in ear.lower() else DEVNAME,
                   "llm": "s109" if "s109" in llm.lower() else DEVNAME,
                   "model": short_model(getattr(self.eng, "last_model", "") or (self.eng.llm[0][2] if self.eng.llm else ""))})

    GREET_SEC = float(os.environ.get("SC_GREET_MIN", "0")) * 60.0     # 既定は無効(起動時の一回だけ)。分を指定すると定期あいさつ

    def _periodic_greet(self) -> None:
        """誰とも話していない時間が続いたら、定期的に「こんにちは、スタックチャンです。」と声をかける。"""
        if self.GREET_SEC <= 0 or self.sg.speaking_since() is not None:
            return
        now = time.time()
        e = self.eng
        quiet = now - max(getattr(e, "last_voice", 0.0), getattr(e, "last_greet", 0.0), getattr(e, "last_hello", 0.0))
        if quiet < self.GREET_SEC or "ja" not in self.pair:
            return
        e.last_greet = now
        self.say("こんにちは、スタックチャンです。", "ja")

    def say(self, text: str, lang: str, face: str = "happy") -> None:
        """ブリッジ発話(あいさつなど)。翻訳せずそのまま読む。"""
        def run():
            try:
                time.sleep(0.8)                                  # ack が端末に届いてから
                pcm = self.eng.speech_pcm(text, lang, voice=self.voice)
                print(f"[bridge] 自発話 {len(pcm)/SR:.1f}秒: {text}", flush=True)
                self._send_pcm(pcm, caption=text, lang=lang)
            except Exception as e:  # noqa: BLE001
                print(f"[bridge] say 失敗: {e}", flush=True)
        threading.Thread(target=run, daemon=True).start()

    # --- 認識→文の区切り→翻訳→読み上げ
    def _worker(self) -> None:
        while self.alive:
            try:
                item = self.segs.get(timeout=0.1)
            except queue.Empty:
                item = "tick"
            now = time.time() - self.t0
            try:
                if item is None:
                    return
                if item == "tick":
                    self._periodic_greet()
                    self._poll_control()
                    if time.time() - getattr(self, "_status_t", 0.0) > 15.0:
                        self.send_status()
                    units = self.ep.tick(now, self.sg.speaking_since())
                else:
                    seg, lo, hi, pre = item
                    self._stage("stt", "active", sec=round(len(seg) / SR, 1))
                    _t = time.time()
                    stt = self.eng.stt
                    text, lang, _p = self._stt(seg)
                    self._stage("stt", "done", ms=int((time.time() - _t) * 1000), text=(text or "")[:40], lang=lang or "",
                                    where=getattr(stt, "last_where", ""))
                    if not text or not lang:
                        continue
                    self.eng.last_voice = time.time()
                    _k = getattr(admin.HUB, "kid", None)
                    if _k is not None and lang == "ja" and _k.switch(self, text):            # 遊びの途中で、べつの遊びを「〜しよう」と言われた: 切り替える
                        continue
                    _g = getattr(admin.HUB, "game", None)
                    if _g is not None and _g.hear(text, lang, getattr(self, "dev_id", "")):   # この機器がしりとり中なら、子どもの声を教材へ(翻訳しない)
                        continue
                    _j = getattr(admin.HUB, "janken", None)
                    if _j is not None and _j.hear(text, lang, getattr(self, "dev_id", "")):   # この機器がじゃんけん中なら、手を教材へ(翻訳しない)
                        continue
                    _q = getattr(admin.HUB, "quiz", None)
                    if _q is not None and _q.hear(text, lang, getattr(self, "dev_id", "")):   # この機器がなぞなぞ・あっち向いてホイ・おみくじ・ものまね中なら、子どもの声を遊びへ(翻訳しない)
                        continue
                    if not getattr(admin.HUB, "edu_on", False) and self._voice_cmd(text):     # 「音量上げて」などは翻訳せず端末を操作する(教材モードでは、言語の切り替えなどの誤爆を防ぐため使わない)
                        continue
                    if _k is not None and _k.hear(self, text, lang):        # 子どもが話しかけた: キャラが返事をする(翻訳しない)
                        continue
                    if getattr(admin.HUB, "edu_on", False):                 # 教材モード: 翻訳はしない(教材と関係ない聞き取りは捨てる)
                        continue
                    units = self.ep.push(seg, lo, hi, text, lang, now=time.time() - self.t0, pre=pre)
                for u in units:
                    self._emit(u)
            except Exception as e:  # noqa: BLE001 - 1発話の失敗で会話を止めない
                print(f"[bridge] 処理エラー: {type(e).__name__}: {e}", flush=True)

    _HALLU = re.compile(r"^(ご視聴(いただき)?ありがとうございました|ありがとうございました|チャンネル登録|お疲れ様でした|"
                        r"thank you( so much)?( for watching)?|thanks for watching|bye|you|cool|okay|oh|uh|hmm|"
                        r"see you( then| next time)?|subscribe)[\s.。!！,、]*$", re.I)

    # 文の切れ目(ここまで訳せたら先に読み始める)。最初の1文は早めに、以降は文末だけ
    _END = re.compile(r"[。！？!?]+|\.(?=\s|$)|\n")
    _COMMA = re.compile(r"[、,，]")

    def _split_ready(self, buf: str, first: bool, lang: str) -> "tuple[list[str], str]":
        """訳のストリームの途中経過から、もう読んでよい文を切り出す。(読む文のリスト, 残り)"""
        out: list = []
        cjk = lang in ("ja", "zh", "ko")
        min_end = 4 if cjk else 14
        min_clause = 5 if cjk else 28                           # 最初のかたまりは読点で早めに切る(声づくりは長さに比例して遅い)
        while True:
            need = (3 if cjk else 5) if (first and not out) else min_end        # 最初の1文は短くても先に読む(最初の音を早く)
            cut = 0
            for m in self._END.finditer(buf):
                if m.end() >= need and (m.end() < len(buf) or not first or out or m.end() >= min_end):
                    cut = m.end()
                    break
            if not cut and (first and not out) and len(buf) >= (min_clause + 4 if cjk else min_clause * 2):       # 最初の1文が長いときは読点で先に
                cm = [c for c in self._COMMA.finditer(buf) if c.end() >= min_clause]
                cut = cm[0].end() if cm else 0
            if not cut:
                break
            out.append(buf[:cut].strip())
            buf = buf[cut:]
            first = False
        return [x for x in out if x], buf

    def _emit(self, u) -> None:
        from tsuyaku.translate import build_messages, out_lang, target_lang, translate_stream
        from tsuyaku.pace import engine_of
        text, lang = u.text, u.lang
        cleaned = clean_hallucination(text, u.hi - u.lo)
        if cleaned != text.strip() and cleaned.strip("。.!！?？ ") != text.strip().strip("。.!！?？ "):
            print(f"[bridge] 幻聴を除去: {text!r} → {cleaned!r}", flush=True)
            admin.HUB.halluc += 1
            self._stage("stt", "done", ms=0, text="(幻聴を除去)")
            if not cleaned:
                return
            text = cleaned
        if self._HALLU.match(text.strip()) and (u.hi - u.lo) < 3.0:   # 雑音から出る定番の幻聴は訳さず捨てる
            print(f"[bridge] 幻聴として破棄: {text}", flush=True)
            return
        target = target_lang(self.pair, lang)
        out = out_lang(self.pair, lang)
        t0 = time.time()
        wait = max(0.0, u.t_ready - u.hi)                    # 話し終わり→文の区切りが決まるまでの待ち
        self.send({"t": "face", "state": "thinking"})
        self._stage("translate", "active", text=text[:40], frm=lang, to=out, model=short_model(self.eng.llm[0][2]) if self.eng.llm else "")
        self.send({"t": "caption", "lang": lang, "orig": text, "trans": "", "final": False})
        msgs = build_messages(self.pair, self.topic, self.history, text, lang)
        src_dur = max(0.0, u.hi - u.speech_lo)

        # 訳しながら、読める文から順に「音づくり→送信」を重ねる(最初の音が出るまでを短く)
        q: "queue.Queue" = queue.Queue()
        stats = {"first_audio": 0.0, "n": 0}

        def speaker():
            end_at = 0.0                                      # 端末で鳴り終わる見込み(単調時計)
            spd = None
            while True:
                item = q.get()
                if item is None:
                    return
                chunk, speed = item
                try:
                    self._stage("tts", "active", text=chunk[:30], lang=target, voice=self.eng.voice_name(target))
                    _t = time.time()
                    pcm = self.eng.speech_pcm(chunk, target, src_dur, speed, voice=self.voice)
                    self._stage("tts", "done", ms=int((time.time() - _t) * 1000), text=chunk[:30], lang=target, voice=self.eng.voice_name(target))
                except Exception as e:  # noqa: BLE001
                    print(f"[bridge] 読み上げの音づくり失敗: {type(e).__name__}: {e}", flush=True)
                    continue
                if len(pcm) == 0:
                    continue
                # 端末は2文ぶんまで受けられる。先に送り過ぎない(今鳴っている文が終わりかけるまで待つ)
                while end_at - time.monotonic() > 2.0 and self.alive:
                    time.sleep(0.05)
                dur = self._send_pcm(pcm)
                end_at = max(end_at, time.monotonic()) + dur
                if not stats["n"]:
                    stats["first_audio"] = time.time() - t0
                stats["n"] += 1

        spk = None
        if target:
            spk = threading.Thread(target=speaker, daemon=True)
            spk.start()
        buf, sent, first, parts = "", "", True, []

        def on_delta(d: str) -> None:
            nonlocal buf, first
            if not target:
                return
            buf += d
            ready, buf = self._split_ready(buf, first, target)
            for r in ready:
                parts.append(r)
                q.put((r, None))
                first = False

        trans, where = "", ""
        order = self.eng.llm_order()
        for label, base, model in order:
            try:
                parts.clear(); buf = ""; first = True
                trans = translate_stream(base, model, msgs, on_delta)
                if trans:
                    where = label
                    self.eng.llm_ok(label)
                    break
            except Exception as e:  # noqa: BLE001
                print(f"[bridge] 翻訳失敗({label}): {str(e)[:100]}", flush=True)
                if len(order) > 1:
                    self.eng.llm_failed(label)
                if parts:                                      # 一部は読み上げ済み: 続きを別サーバーで訳すと重複するので打ち切る
                    trans = " ".join(parts)
                    where = label
                    break
        t_tr = time.time() - t0
        self._stage("translate", "done", ms=int(t_tr * 1000), text=(trans or "")[:60], where=where, model=short_model(getattr(self.eng, "last_model", "")))
        if target and trans:
            rest = buf.strip()
            if rest:
                q.put((rest, None))
        if spk is not None:
            q.put(None)
        if not trans:
            self.send({"t": "face", "state": "sad"})
            self.send({"t": "caption", "lang": lang, "orig": text, "trans": "(翻訳できませんでした)", "final": True})
            return
        self.history.append({"lang": lang, "orig": text, "trans": trans})
        self.history = self.history[-8:]
        self.eng.last_llm = where
        self._log_turn(lang, out, text, trans, where)
        self.send_status()
        print(f"[bridge] {lang}→{out} ({where}, 区切り待ち {wait:.2f}s, 翻訳 {t_tr:.2f}s) {text} ⇝ {trans}", flush=True)
        self.send({"t": "caption", "lang": lang, "to": out, "orig": text, "trans": trans, "final": True})
        if not target:                                  # ペア外の言語は字幕だけ
            self.send({"t": "face", "state": "neutral"})
            return
        if spk is not None:
            spk.join(timeout=40)
        mood = self._mood(text + " " + trans)
        if mood:                                        # 話の内容に合わせたひと言リアクション
            self.send({"t": "face", "state": mood})
        if stats["n"]:
            admin.HUB.add_latency(stats["first_audio"])
            print(f"[bridge] 訳し始めから最初の音まで {stats['first_audio']:.2f}s "
                  f"({stats['n']}文, 話速×{self.eng.last_speed})", flush=True)
        else:
            self.send({"t": "face", "state": "neutral"})

    _HAPPY = re.compile(r"ありがとう|うれしい|嬉しい|楽しい|すごい|やった|最高|おめでとう|大好き|thank|great|awesome|wonderful|congrat|love|happy|glad|amazing", re.I)
    _SAD = re.compile(r"ごめん|すみません|残念|悲しい|つらい|辛い|困った|だめ|最悪|sorry|sad|unfortunately|terrible|worried|too bad", re.I)

    def _mood(self, text: str) -> str:
        if self._SAD.search(text):
            return "sad"
        if self._HAPPY.search(text):
            return "happy"
        return ""

    def _log_turn(self, lang: str, out: str, orig: str, trans: str, where: str) -> None:
        """会話ログを bridge/logs/日付.jsonl に追記(このMacの中だけ。git管理外)。SC_NO_LOG=1 で保存しない。"""
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "from": lang, "to": out, "orig": orig, "trans": trans, "engine": where, "dev": self.name}
        admin.HUB.add_turn(rec)                              # 管理画面の「直近の通訳」(メモリ内だけ)
        if os.environ.get("SC_NO_LOG"):
            return
        try:
            d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, time.strftime("%Y-%m-%d") + ".jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] ログ保存失敗: {e}", flush=True)

    def _send_pcm(self, pcm: np.ndarray, caption: str = "", lang: str = "", _resend: bool = False) -> float:
        # 始めと終わりを 15ms でなだらかにして、再生のぷつ音を防ぐ。末尾に 0.15 秒の無音
        f = pcm.astype(np.float32)
        k = min(len(f) // 2, int(0.015 * SR))
        if k > 0:
            ramp = np.linspace(0.0, 1.0, k, dtype=np.float32)
            f[:k] *= ramp
            f[-k:] *= ramp[::-1]
        pcm = np.concatenate([f, np.zeros(int(0.05 * SR), np.float32)]).astype("<i2")
        dur = len(pcm) / SR
        if caption:
            self.send({"t": "caption", "lang": lang, "to": lang, "orig": caption, "trans": caption, "final": True})
        self._stage("play", "active", until=time.time() + dur, dur=round(dur, 1))
        self.muted_until = max(self.muted_until, time.time()) + dur + 0.8   # 再生+残響ぶん聞かない(文ごとに延長)
        raw = pcm.tobytes()
        with self._tx_lock:                              # 1つの音声は途切れず送る(別の音声と混ざらないように)
            return self._send_locked(pcm, raw, dur, _resend)

    def _send_locked(self, pcm: np.ndarray, raw: bytes, dur: float, _resend: bool) -> float:
        self._seq = getattr(self, "_seq", 0) + 1
        seq = self._seq
        self._sent = getattr(self, "_sent", {})
        self._sent[seq] = (pcm, dur)
        for k in [k for k in self._sent if k < seq - 4]:
            del self._sent[k]
        self.send({"t": "speech", "sr": SR, "ms": int(dur * 1000), "bytes": len(pcm) * 2, "seq": seq})
        for i in range(0, len(raw), 1024):
            self.send(bytes([TAG_SPEECH]) + raw[i:i + 1024])
            time.sleep(0.005)                            # 端末の受信が追いつくよう少し間を置く
        self.send({"t": "speech_end", "bytes": len(raw), "seq": seq})
        return dur


# ---------------------------------------------------------------- WebSocket サーバー
def save_screen(jpeg: bytes, sess=None) -> None:
    """端末の画面のスクリーンショット(デバッグ用)を SC_SNAP_DIR/screen.jpg に保存。"""
    admin.HUB.screen, admin.HUB.screen_t = jpeg, time.time()
    if sess is not None and sess.dev_id:
        admin.HUB.screens[sess.dev_id] = jpeg
    d = os.environ.get("SC_SNAP_DIR")
    if d:
        with open(os.path.join(d, "screen.jpg"), "wb") as f:
            f.write(jpeg)


async def advertise(port: int):
    """LAN に「ここにブリッジがいる」と告知する(mDNS)。端末はこれを探すので、Mac のアドレスを設定しなくてよい。"""
    try:
        import socket
        from zeroconf import ServiceInfo
        from zeroconf.asyncio import AsyncZeroconf
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(("192.0.2.1", 9))                       # 実際には送らない。この Mac の LAN アドレスを知るため
        ip = sk.getsockname()[0]
        sk.close()
        name = "sokutsu-" + socket.gethostname().split(".")[0].replace(" ", "-")[:20]
        info = ServiceInfo("_sokutsu-bridge._tcp.local.", f"{name}._sokutsu-bridge._tcp.local.",
                           addresses=[socket.inet_aton(ip)], port=port, properties={"v": "1"})
        zc = AsyncZeroconf()
        await zc.async_register_service(info)
        print(f"[bridge] mDNS 告知: _sokutsu-bridge._tcp {ip}:{port}", flush=True)
        return zc
    except Exception as e:  # noqa: BLE001
        print(f"[bridge] mDNS 告知に失敗(端末は設定したアドレスを使います): {type(e).__name__}: {e}", flush=True)
        return None


async def serve(host: str, port: int, eng: Engine) -> None:
    import websockets
    _zc = await advertise(port)

    async def handler(ws):
        loop = asyncio.get_running_loop()

        def send(msg):  # ワーカースレッドから安全に送る
            data = json.dumps(msg, ensure_ascii=False) if isinstance(msg, dict) else msg
            asyncio.run_coroutine_threadsafe(ws.send(data), loop)

        sess = Session(eng, send)
        print(f"[bridge] 接続: {ws.remote_address}", flush=True)
        acked = False

        def ack():
            nonlocal acked
            acked = True
            send({"t": "hello_ack", "pair": list(sess.pair), "ear": "s109" if eng.remote_ear else "mac"})
        if not admin.HUB.usb_live():                       # USB でつながっている機器があるときは、Wi-Fi への即答はしない(機器を特定してから決める)
            ack()
        sess.hello()
        try:
            async for m in ws:
                if isinstance(m, str):
                    j = json.loads(m)
                    if j.get("t") in ("batt", "event", "vol", "dbg"):
                        sess.device_event(j)
                    if j.get("t") == "dbg":
                        print(f"[bridge] 端末: {j}", flush=True)
                        if j.get("short") and j.get("seq"):
                            sess.resend(int(j["seq"]))
                    if j.get("t") == "hello" and not sess.dev_id:
                        sess.identify(j, (ws.remote_address or ("", 0))[0])
                    if j.get("t") == "hello" and not acked:
                        if admin.HUB.usb_session(sess.dev_id, sess):       # この機器は USB でつながっている: USB を優先し、Wi-Fi 側は使わない(二重にならない)
                            sess.alive = False
                            print(f"[bridge] {sess.name}: USB 接続を優先します(Wi-Fi 側は閉じます)", flush=True)
                            break
                        ack()
                    if j.get("t") == "hello":
                        sess.hello()
                    if j.get("t") == "hello" or j.get("t") == "config":
                        if "pair" in j and len(j["pair"]) == 2:
                            sess.apply_pair(tuple(j["pair"]))
                        sess.topic = j.get("topic", sess.topic)
                    continue
                if not m:
                    continue
                tag, body = m[0], m[1:]
                if tag == TAG_AUDIO:
                    sess.feed_pcm(body)
                elif tag == TAG_AUDIO_SEQ:
                    sess.feed_seq(body)
                elif tag == TAG_JPEG:
                    await loop.run_in_executor(None, sess.on_frame, body)
                elif tag == TAG_SCREEN:
                    save_screen(body, sess)
        except Exception as e:  # noqa: BLE001 - 切断
            import traceback
            print(f"[bridge] 切断: {type(e).__name__}: {e}", flush=True)
            if not isinstance(e, (ConnectionError, OSError)) and "ConnectionClosed" not in type(e).__name__:
                traceback.print_exc()
        finally:
            sess.close()

    async with websockets.serve(handler, host, port, max_size=2 ** 22, ping_interval=10, ping_timeout=20):
        print(f"[bridge] 待ち受け ws://{host}:{port}  (StackChan の bridge_host をこの Mac の LAN アドレスに)", flush=True)
        await asyncio.Future()


# ---------------------------------------------------------------- USB シリアル(有線)
def serve_serial(port: str, eng: Engine, alive=None) -> None:
    """フレーム A5 5A | 種別(1=テキスト,2=バイナリ) | 長さ u32 LE | 本体。WebSocket 版と同じ中身。"""
    import serial  # pyserial
    warned = False
    while True:
        if alive is not None and not alive():               # 自動検出: この機器の USB が抜かれた/書き込み中なら、この接続は終わり
            return
        try:
            ser = serial.Serial(None, 921600, timeout=0.2)
            ser.port = port
            ser.dtr = False
            ser.rts = False
            ser.open()
        except Exception as e:  # noqa: BLE001
            if not warned:                                  # USB が無いのは普通(Wi-Fi で動く)ので、最初の1回だけ知らせる
                print(f"[bridge] USB {port} は未接続です(Wi-Fi で動作)。つながれば自動で使います", flush=True)
                warned = True
            time.sleep(3)
            continue
        lock = threading.Lock()

        def send(msg):
            data = json.dumps(msg, ensure_ascii=False).encode() if isinstance(msg, dict) else msg
            kind = 1 if isinstance(msg, dict) else 2
            with lock:
                try:
                    ser.write(bytes([0xA5, 0x5A, kind]) + len(data).to_bytes(4, "little") + data)
                except Exception:  # noqa: BLE001
                    pass

        warned = False
        sess = Session(eng, send)
        print(f"[bridge] USB 接続: {port}", flush=True)
        buf = bytearray()                                  # ack は端末の hello に答える(ポートを開くと端末は再起動する)
        try:
            while True:
                if alive is not None and not alive():
                    break
                buf += ser.read(4096)
                while True:
                    i = buf.find(b"\xa5\x5a")
                    if i < 0:
                        buf.clear() if len(buf) > 8192 else None
                        break
                    if i:
                        del buf[:i]            # ログなど枠外の文字は捨てる
                    if len(buf) < 7:
                        break
                    kind, n = buf[2], int.from_bytes(buf[3:7], "little")
                    if n > 1 << 22:
                        del buf[:2]
                        continue
                    if len(buf) < 7 + n:
                        break
                    body = bytes(buf[7:7 + n]); del buf[:7 + n]
                    if kind == 1:
                        j = json.loads(body.decode("utf-8", "ignore") or "{}")
                        if j.get("t") in ("batt", "event", "vol", "dbg"):
                            sess.device_event(j)
                        if j.get("t") == "dbg":
                            print(f"[bridge] 端末: {j}", flush=True)
                            if j.get("short") and j.get("seq"):
                                sess.resend(int(j["seq"]))
                        if j.get("t") == "hello" and not sess.dev_id:
                            sess.identify(j, "usb")
                        if j.get("t") == "hello":
                            sess.hello()
                            if "pair" in j and len(j["pair"]) == 2:
                                sess.apply_pair(tuple(j["pair"]))
                            send({"t": "hello_ack", "pair": list(sess.pair), "ear": "s109" if eng.remote_ear else "mac"})
                    elif body:
                        tag, rest = body[0], body[1:]
                        if tag == TAG_AUDIO:
                            sess.feed_pcm(rest)
                        elif tag == TAG_AUDIO_SEQ:
                            sess.feed_seq(rest)
                        elif tag == TAG_JPEG:
                            sess.on_frame(rest)
                        elif tag == TAG_SCREEN:
                            save_screen(rest, sess)
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] USB 切断: {type(e).__name__}: {e}", flush=True)
        finally:
            sess.close()
            try:
                ser.close()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--engine", default="auto", help="auto(s109→Mac) / s109 / mac")
    ap.add_argument("--serial", default="", help="USB 有線: シリアルポート(例 /dev/cu.usbmodem101)")
    a = ap.parse_args()
    eng = Engine(a.engine)
    if os.environ.get("SC_NO_ADMIN") != "1":
        admin.start(eng, int(os.environ.get("SC_ADMIN_PORT", "8080")))
    import autonomy
    import discuss
    admin.HUB.discuss = discuss.Discussion(admin.HUB, admin.REG, lambda: eng)
    import shiritori
    admin.HUB.game = shiritori.GameHub(admin.HUB, admin.REG, admin.HUB.discuss, lambda: eng)
    import janken
    admin.HUB.janken = janken.JankenHub(admin.HUB, admin.REG, admin.HUB.discuss, lambda: eng)
    import quiz
    admin.HUB.quiz = quiz.QuizHub(admin.HUB, admin.REG, admin.HUB.discuss, lambda: eng)
    import lesson
    admin.HUB.lesson = lesson.Lesson(admin.HUB)
    _hubs = [admin.HUB.game, admin.HUB.janken, admin.HUB.quiz]
    for _h in _hubs:
        _h.peers = [x for x in _hubs if x is not _h]              # 同じ機器で 2 つの教材(しりとり・じゃんけん・ほかの遊び)を同時に動かさない
    admin.HUB.kid = kidchat.KidChat(admin.HUB)
    try:                                                              # 遊びの定型の台詞を、背景で先に声にしておく(待ち時間の短縮)
        import warm as _warm

        def _warm_busy() -> bool:
            h = admin.HUB
            k = getattr(h, "kid", None)
            return bool(any(getattr(x, "on", False) for x in (h.game, h.janken, h.quiz)) or (k is not None and k.speaking) or (h.discuss is not None and h.discuss.on)
                        or time.time() - getattr(eng, "last_voice", 0.0) < 6.0)

        def _warm_names() -> list:
            from discuss import PERSONAS
            ids = [r["id"] for r in admin.REG.all()] if admin.REG is not None else []
            names = [PERSONAS[admin.HUB.discuss.persona_of(i) % len(PERSONAS)]["name"] for i in ids[:3]]
            return names or [PERSONAS[0]["name"]]

        def _voicevox_up() -> bool:
            u = getattr(getattr(eng, "s", None), "voicevox_url", "") or getattr(getattr(eng, "tts", None), "voicevox_url", "")
            if not u:
                return True
            import requests
            try:
                return requests.get(u.rstrip("/") + "/version", timeout=1.5).ok
            except Exception:  # noqa: BLE001
                return False
        admin.HUB.warm = _warm.Warmer(lambda: eng, _warm_names, busy=_warm_busy, voice_ok=_voicevox_up)
        threading.Thread(target=admin.HUB.warm.run, daemon=True).start()
    except Exception as e:  # noqa: BLE001
        print("声の先読みを始められません:", e, flush=True)
    threading.Thread(target=admin.HUB.kid.run, daemon=True).start()           # 子どもが黙ったとき・静かなときに、自分から話しかける
    import menuboard
    menuboard.start(admin.HUB)                                                 # 教材モード: 遊んでいない機器の画面に、言える遊びの一覧を出す
    admin.HUB.auto = autonomy.Autonomy(admin.HUB, admin.REG, admin.HUB.discuss, lambda: eng)
    threading.Thread(target=admin.HUB.auto.run, daemon=True).start()
    try:                                                              # Ubuntu の USB に挿した機器を認識し、新品なら書き込む
        import usbprov
        admin.HUB.usb = usbprov.Watcher(admin.HUB, admin.REG)
        threading.Thread(target=admin.HUB.usb.run, daemon=True).start()
    except Exception as e:  # noqa: BLE001
        print("usb watcher off:", e)
    threading.Thread(target=eng.watch, daemon=True).start()          # s109 が後から使えるようになったら切り替える
    if a.serial:                                    # USB は別スレッド。Wi-Fi(WebSocket)も同時に待ち受ける
        threading.Thread(target=serve_serial, args=(a.serial, eng), daemon=True).start()
    if os.environ.get("SC_USB_AUTO", "1") != "0":   # USB に挿した機器(何台でも)を自動で見つけて、機器ごとに別の接続として扱う
        import usblink
        admin.HUB.usblinks = usblink.UsbLinks(lambda port, alive: serve_serial(port, eng, alive), watcher=getattr(admin.HUB, "usb", None))
        if getattr(admin.HUB, "usb", None) is not None:
            admin.HUB.usb.release, admin.HUB.usb.resume = admin.HUB.usblinks.release, admin.HUB.usblinks.resume
        threading.Thread(target=admin.HUB.usblinks.run, daemon=True).start()
    if os.environ.get("SC_NO_WS") == "1":          # Wi-Fi(WebSocket)の受付を止める(Wi-Fi が無い現場の再現・検証用)
        print("[bridge] Wi-Fi(WebSocket)の受付は止めています(SC_NO_WS=1)。USB だけで動作", flush=True)
        threading.Event().wait()
    asyncio.run(serve(a.host, a.port, eng))


if __name__ == "__main__":
    main()
