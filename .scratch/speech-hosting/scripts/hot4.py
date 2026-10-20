import numpy as np, sherpa_onnx, sys
from pathlib import Path
from kokoro_onnx import Kokoro
D=Path("models/kroko-en"); S=Path(sys.argv[1])
# vocab for sentencepiece-style encoding: piece<TAB>score
with open(S/"kroko.vocab","w",encoding="utf-8") as f:
    for l in open(D/"tokens.txt",encoding="utf-8"):
        p,i=l.rsplit(" ",1); f.write(f"{p}\t{-float(i)/100:.4f}\n")
kp=sorted(Path("models/kokoro-v1.0").glob("*.onnx")); print(kp)
k=Kokoro(str(kp[0]),"models/kokoro-v1.0/voices-v1.0.bin")
sents=["Play Opalite by Taylor Swift","Call Jocelyn and then text Angelo","Put on Pantropiko by BINI","Play Tadhana by Up Dharma Down","Message Ysabel Dela Cruz about Jollibee"]
words={"opalite","taylor swift","jocelyn","angelo","pantropiko","bini","tadhana","up dharma down","ysabel","dela cruz","jollibee"}
hw=S/"hot.txt"; hw.write_text("\n".join(f"{f} :3.0" for w in sorted(words) for f in {w.lower(),w.title(),w.upper()})+"\n")
def rec(**kw):
    return sherpa_onnx.OnlineRecognizer.from_transducer(tokens=str(D/"tokens.txt"),encoder=str(D/"encoder.onnx"),decoder=str(D/"decoder.onnx"),joiner=str(D/"joiner.onnx"),num_threads=2,sample_rate=16000,feature_dim=80,**kw)
def dec(r,x):
    x=np.concatenate([np.zeros(8000,np.float32),x,np.zeros(24000,np.float32)])
    s=r.create_stream(); s.accept_waveform(16000,x); s.input_finished()
    while r.is_ready(s): r.decode_stream(s)
    return r.get_result(s) if isinstance(r.get_result(s),str) else r.get_result(s).text
import scipy.signal as sg
clips=[]
for t in sents:
    a,sr=k.create(t,voice="am_michael",speed=1.0,lang="en-us"); clips.append(sg.resample_poly(a,16000,sr).astype(np.float32))
base=rec(decoding_method="modified_beam_search",max_active_paths=4)
hot=rec(decoding_method="modified_beam_search",max_active_paths=4,hotwords_file=str(hw),hotwords_score=3.0,modeling_unit="bpe",bpe_vocab=str(S/"kroko.vocab"))
for t,x in zip(sents,clips): print(f"REF  {t}\nBASE {dec(base,x)}\nHOT  {dec(hot,x)}\n")
