import json, re, sys, wave, numpy as np, sherpa_onnx
from pathlib import Path
D=Path("models/kroko-en"); M=Path("bench/mic_turns")
refs=json.loads((M/"refs.json").read_text()) if (M/"refs.json").exists() else None
items=refs if isinstance(refs,list) else [{"id":k,"text":v} for k,v in refs.items()]
def norm(s): return re.sub(r"[^a-z' ]"," ",s.lower()).split()
def ed(a,b):
    d=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        p,d[0]=d[0],i
        for j,y in enumerate(b,1):
            p,d[j]=d[j],min(d[j]+1,d[j-1]+1,p+(x!=y))
    return d[-1]
def load(p):
    w=wave.open(str(p)); x=np.frombuffer(w.readframes(w.getnframes()),np.int16).astype(np.float32)/32768; return x
def rec(**kw):
    return sherpa_onnx.OnlineRecognizer.from_transducer(tokens=str(D/"tokens.txt"),encoder=str(D/"encoder.onnx"),decoder=str(D/"decoder.onnx"),joiner=str(D/"joiner.onnx"),num_threads=2,sample_rate=16000,feature_dim=80,**kw)
def run(r,hot=None):
    err=n=0; probs=None
    for it in items:
        f=M/(it.get("file") or (str(it.get("id"))+".wav"))
        if not f.exists(): continue
        x=load(f); x=np.concatenate([np.zeros(8000,np.float32),x,np.zeros(24000,np.float32)])
        s=r.create_stream(hot) if hot else r.create_stream()
        s.accept_waveform(16000,x); s.input_finished()
        while r.is_ready(s): r.decode_stream(s)
        res=r.get_result_all(s) if hasattr(r,"get_result_all") else None
        t=r.get_result(s); t=t if isinstance(t,str) else t.text
        ref=norm(it["text"]); err+=ed(norm(t),ref); n+=len(ref)
    return err/n,n
print("items keys:",list(items[0].keys()), len(items))
for name,kw in [("greedy",dict(decoding_method="greedy_search")),
 *[(f"greedy bp={b}",dict(decoding_method="greedy_search",blank_penalty=b)) for b in (0.5,1.0,2.0)],
 ("mbs4",dict(decoding_method="modified_beam_search",max_active_paths=4)),
 *[(f"mbs4 bp={b}",dict(decoding_method="modified_beam_search",max_active_paths=4,blank_penalty=b)) for b in (0.5,1.0,2.0)]]:
    print(name, run(rec(**kw)))
