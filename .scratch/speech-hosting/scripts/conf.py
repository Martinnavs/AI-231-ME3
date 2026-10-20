import json,sys,difflib,importlib.util,numpy as np,sherpa_onnx as so
from pathlib import Path
spec=importlib.util.spec_from_file_location("sc","scripts/stt_compare.py");sc=importlib.util.module_from_spec(spec);sys.modules["sc"]=sc;spec.loader.exec_module(sc)
d=Path("models/stt-candidates/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8")
r=so.OfflineRecognizer.from_transducer(encoder=str(d/"encoder.int8.onnx"),decoder=str(d/"decoder.int8.onnx"),joiner=str(d/"joiner.int8.onnx"),tokens=str(d/"tokens.txt"),num_threads=4,sample_rate=16000,feature_dim=80,model_type="nemo_transducer",decoding_method="greedy_search")
items=json.load(open("bench/stt_hard.json")); rows=[]
first=True
for it in items:
    pcm=sc.read_wav(Path("bench/mic_hard")/f"{it['id']}.wav"); x=pcm.astype(np.float32)/32768
    x=np.concatenate([np.zeros(8000,np.float32),x,np.zeros(24000,np.float32)])
    s=r.create_stream(); s.accept_waveform(16000,x); r.decode_stream(s); res=s.result
    if first:
        print([a for a in dir(res) if not a.startswith("_")]); print(list(res.tokens)[:8], list(getattr(res,"ys_log_probs",[]))[:8]); first=False
    lp=list(getattr(res,"ys_log_probs",[])); toks=list(res.tokens)
    if len(lp)!=len(toks): print("len mismatch",it["id"],len(lp),len(toks)); continue
    # group tokens to words: a token starting with space (or first) begins a word
    words=[];cur=[];cl=[]
    for t,l in zip(toks,lp):
        if t.startswith(" ") and cur: words.append(("".join(cur),cl)); cur=[];cl=[]
        cur.append(t); cl.append(l)
    if cur: words.append(("".join(cur),cl))
    hw=[];conf=[]
    for w,cl in words:
        nw=sc.norm_words(w)
        for k in nw: hw.append(k); conf.append((float(np.exp(np.mean(cl))),float(np.min(cl))))
    ref=sc.norm_words(it["text"]); sm=difflib.SequenceMatcher(a=ref,b=hw,autojunk=False)
    ok=[False]*len(hw)
    for tag,i1,i2,j1,j2 in sm.get_opcodes():
        if tag=="equal":
            for j in range(j1,j2): ok[j]=True
    ents=set(w for e in it["entities"] for w in sc.norm_words(e))
    for j,w in enumerate(hw): rows.append((w,ok[j],conf[j][0],conf[j][1],it["id"]))
    # entity-word level (reference words in entity)
a=np.array([[c,m,o] for _,o,c,m,_ in rows],float)
print("hyp words",len(rows),"wrong",int((a[:,2]==0).sum()))
def auroc(score,pos):  # P(score_pos > score_neg)
    p=score[pos];n=score[~pos]
    return float(np.mean([(x>n).mean()+0.5*(x==n).mean() for x in p]))
okm=a[:,2]==1
print("AUROC word-conf(geo-mean) separating correct from wrong:",round(auroc(a[:,0],okm),3))
print("AUROC min-token-logprob:",round(auroc(a[:,1],okm),3))
for th in (0.5,0.6,0.7,0.8,0.9,0.95):
    fl=a[:,0]<th; print(f"flag conf<{th}: flagged {fl.sum():3d} ({fl.mean():.0%} of words), of which truly wrong {int(((a[:,2]==0)&fl).sum()):3d} -> precision {((a[:,2]==0)&fl).sum()/max(fl.sum(),1):.0%}, recall of wrong {((a[:,2]==0)&fl).sum()/max((a[:,2]==0).sum(),1):.0%}")
json.dump([list(map(lambda v: v if not isinstance(v,(np.floating,np.bool_)) else float(v),row)) for row in rows],open("out/stt_compare/parakeet-wordconf.json","w"))
