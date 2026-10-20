import json,sys,time,importlib.util,numpy as np,sherpa_onnx as so
from pathlib import Path
spec=importlib.util.spec_from_file_location("sc","scripts/stt_compare.py");sc=importlib.util.module_from_spec(spec);sys.modules["sc"]=sc;spec.loader.exec_module(sc)
C=Path("models/stt-candidates")
def parakeet(v): 
    d=C/f"sherpa-onnx-nemo-parakeet-tdt-0.6b-{v}-int8"
    return so.OfflineRecognizer.from_transducer(encoder=str(d/"encoder.int8.onnx"),decoder=str(d/"decoder.int8.onnx"),joiner=str(d/"joiner.int8.onnx"),tokens=str(d/"tokens.txt"),num_threads=4,sample_rate=16000,feature_dim=80,model_type="nemo_transducer",decoding_method="greedy_search")
def moon():
    d=C/"sherpa-onnx-moonshine-base-en-int8"
    return so.OfflineRecognizer.from_moonshine(preprocessor=str(d/"preprocess.onnx"),encoder=str(d/"encode.int8.onnx"),uncached_decoder=str(d/"uncached_decode.int8.onnx"),cached_decoder=str(d/"cached_decode.int8.onnx"),tokens=str(d/"tokens.txt"),num_threads=4)
cands={"parakeet-v2":lambda:parakeet("v2"),"parakeet-v3":lambda:parakeet("v3"),"moonshine-base":moon}
sets={"hard":("bench/mic_hard","bench/stt_hard.json"),"easy":("bench/mic_turns","bench/mic_turns/refs.json")}
out={}
for name,mk in cands.items():
    try: r=mk()
    except Exception as e: print(name,"LOAD FAIL",repr(e)[:300]); continue
    for sn,(ad,rf) in sets.items():
        items=[i for i in json.load(open(rf)) if i["kind"]!="noise"]
        edits=nw=eh=et=0; lat=[]; comp=aud=0; rows=[]
        for it in items:
            pcm=sc.read_wav(Path(ad)/f"{it['id']}.wav"); x=pcm.astype(np.float32)/32768 if pcm.dtype==np.int16 else pcm.astype(np.float32)
            x=np.concatenate([np.zeros(8000,np.float32),x,np.zeros(24000,np.float32)])
            t=time.perf_counter(); s=r.create_stream(); s.accept_waveform(16000,x); r.decode_stream(s); dt=time.perf_counter()-t
            txt=s.result.text; lat.append(dt*1000); comp+=dt; aud+=len(x)/16000
            h=sc.norm_words(txt); ref=sc.norm_words(it["text"]); edits+=sc.word_edits(ref,h); nw+=len(ref)
            ht=" "+" ".join(h)+" "
            for e in it["entities"]: et+=1; eh+=f" {' '.join(sc.norm_words(e))} " in ht
            rows.append((it["id"],it["text"],txt))
        print(f"{name:15} {sn}: WER {edits/nw:.3f} entities {eh}/{et}={eh/max(et,1):.0%} decode p50 {np.percentile(lat,50):.0f} p95 {np.percentile(lat,95):.0f} ms RTF {comp/aud:.3f}",flush=True)
        out[f"{name}/{sn}"]=rows
json.dump(out,open("out/stt_compare/offline-rows.json","w"),indent=1)
