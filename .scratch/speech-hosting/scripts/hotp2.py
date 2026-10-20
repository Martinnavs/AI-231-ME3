import sys,importlib.util,numpy as np,sherpa_onnx as so,json,time
from pathlib import Path
spec=importlib.util.spec_from_file_location("sc","scripts/stt_compare.py");sc=importlib.util.module_from_spec(spec);sys.modules["sc"]=sc;spec.loader.exec_module(sc)
S=Path(sys.argv[1]); d=Path("models/stt-candidates/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8")
base=dict(encoder=str(d/"encoder.int8.onnx"),decoder=str(d/"decoder.int8.onnx"),joiner=str(d/"joiner.int8.onnx"),tokens=str(d/"tokens.txt"),num_threads=4,sample_rate=16000,feature_dim=80,model_type="nemo_transducer")
items=json.load(open("bench/stt_hard.json"))
wav={it["id"]:np.concatenate([np.zeros(8000,np.float32),sc.read_wav(Path("bench/mic_hard")/f"{it['id']}.wav").astype(np.float32)/32768,np.zeros(24000,np.float32)]) for it in items}
def score(r,tag):
    e=n=eh=et=0;lat=[]
    for it in items:
        t=time.perf_counter(); s=r.create_stream(); s.accept_waveform(16000,wav[it["id"]]); r.decode_stream(s); lat.append((time.perf_counter()-t)*1000)
        h=sc.norm_words(s.result.text); ref=sc.norm_words(it["text"]); e+=sc.word_edits(ref,h); n+=len(ref)
        ht=" "+" ".join(h)+" "
        for en in it["entities"]: et+=1; eh+=f" {' '.join(sc.norm_words(en))} " in ht
    print(f"{tag:42} WER {e/n:.3f} entities {eh}/{et}={eh/et:.0%} p50 {np.percentile(lat,50):.0f} ms",flush=True)
score(so.OfflineRecognizer.from_transducer(decoding_method="greedy_search",**base),"greedy")
for k in (4,8): score(so.OfflineRecognizer.from_transducer(decoding_method="modified_beam_search",max_active_paths=k,**base),f"mbs paths={k}")
ents=sorted({e for it in items for e in it["entities"]})
# vocab file for bpe tokenization of hotwords
with open(S/"pk.vocab","w",encoding="utf-8") as f:
    for l in open(d/"tokens.txt",encoding="utf-8"):
        p,i=l.rstrip("\n").rsplit(" ",1); f.write(f"{p}\t{-float(i)/100:.4f}\n")
def forms(e): return {e.lower(),e.title()}
for sc_ in (1.0,2.0,3.0):
    hw=S/"pk_hot.txt"; hw.write_text("\n".join(f"{f} :{sc_}" for e in ents for f in forms(e))+"\n")
    for mu in ("bpe",):
        try: score(so.OfflineRecognizer.from_transducer(decoding_method="modified_beam_search",max_active_paths=4,hotwords_file=str(hw),hotwords_score=sc_,modeling_unit=mu,bpe_vocab=str(S/"pk.vocab"),**base),f"mbs4 + hotwords {mu} score={sc_}")
        except Exception as ex: print("FAIL",mu,sc_,repr(ex)[:200])
