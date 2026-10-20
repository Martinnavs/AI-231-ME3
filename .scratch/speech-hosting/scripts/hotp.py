import sys,importlib.util,numpy as np,sherpa_onnx as so,json
from pathlib import Path
spec=importlib.util.spec_from_file_location("sc","scripts/stt_compare.py");sc=importlib.util.module_from_spec(spec);sys.modules["sc"]=sc;spec.loader.exec_module(sc)
d=Path("models/stt-candidates/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8")
print(sorted(l.strip() for l in open(d/"tokens.txt"))[:0], sum(1 for _ in open(d/"tokens.txt")))
base=dict(encoder=str(d/"encoder.int8.onnx"),decoder=str(d/"decoder.int8.onnx"),joiner=str(d/"joiner.int8.onnx"),tokens=str(d/"tokens.txt"),num_threads=4,sample_rate=16000,feature_dim=80,model_type="nemo_transducer")
items=json.load(open("bench/stt_hard.json"))
def run(r):
    out=[]
    for it in items[:59]:
        pcm=sc.read_wav(Path("bench/mic_hard")/f"{it['id']}.wav"); x=pcm.astype(np.float32)/32768
        x=np.concatenate([np.zeros(8000,np.float32),x,np.zeros(24000,np.float32)])
        s=r.create_stream(); s.accept_waveform(16000,x); r.decode_stream(s); out.append(s.result.text)
    return out
g=run(so.OfflineRecognizer.from_transducer(decoding_method="greedy_search",**base)); print("greedy ok")
try:
    m=run(so.OfflineRecognizer.from_transducer(decoding_method="modified_beam_search",**base))
    ch=sum(a!=b for a,b in zip(g,m)); print("modified_beam_search runs; clips differing from greedy:",ch)
    for a,b in list((a,b) for a,b in zip(g,m) if a!=b)[:5]: print(" G:",a,"\n M:",b)
    ents=sorted({e.lower() for it in items for e in it["entities"]})
    hw=Path(sys.argv[1])/"pk_hot.txt"; hw.write_text("\n".join(f"{e} :2.0" for e in ents)+"\n")
    try:
        h=run(so.OfflineRecognizer.from_transducer(decoding_method="modified_beam_search",hotwords_file=str(hw),hotwords_score=2.0,**base))
        print("hotwords run; clips differing from mbs:",sum(a!=b for a,b in zip(m,h)))
        for a,b in list((a,b) for a,b in zip(m,h) if a!=b)[:8]: print(" M:",a,"\n H:",b)
    except Exception as e: print("hotwords FAIL",repr(e)[:300])
except Exception as e: print("mbs FAIL",repr(e)[:300])
