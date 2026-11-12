"""PortAudio が無いサーバー用の代用(ブリッジは端末からの音声を使うので、マイク/スピーカーは不要)。"""
class InputStream:
    def __init__(self, *a, **k): raise RuntimeError("no audio device on this server")
class OutputStream(InputStream): pass
def query_devices(*a, **k): return []
def play(*a, **k): pass
def stop(*a, **k): pass
def wait(*a, **k): pass
