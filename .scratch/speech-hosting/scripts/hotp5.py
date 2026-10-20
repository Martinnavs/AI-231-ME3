import sys,importlib.util,numpy as np,sherpa_onnx as so,json,time
from pathlib import Path
spec=importlib.util.spec_from_file_location("sc","scripts/stt_compare.py");sc=importlib.util.module_from_spec(spec);sys.modules["sc"]=sc;spec.loader.exec_module(sc)
S=Path(sys.argv[1]); d=Path("models/stt-candidates/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8")
base=dict(encoder=str(d/"encoder.int8.onnx"),decoder=str(d/"decoder.int8.onnx"),joiner=str(d/"joiner.int8.onnx"),tokens=str(d/"tokens.txt"),num_threads=4,sample_rate=16000,feature_dim=80,model_type="nemo_transducer")
items=[i for i in json.load(open("bench/mic_turns/refs.json")) if i["kind"]!="noise"]
wav={it["id"]:np.concatenate([np.zeros(8000,np.float32),sc.read_wav(Path("bench/mic_turns")/f"{it['id']}.wav").astype(np.float32)/32768,np.zeros(24000,np.float32)]) for it in items}
def score(r,tag):
    e=n=0;txt=[]
    for it in items:
        s=r.create_stream(); s.accept_waveform(16000,wav[it["id"]]); r.decode_stream(s)
        h=sc.norm_words(s.result.text); ref=sc.norm_words(it["text"]); e+=sc.word_edits(ref,h); n+=len(ref); txt.append(s.result.text)
    print(f"{tag:40} EASY WER {e/n:.3f}",flush=True); return txt
g=score(so.OfflineRecognizer.from_transducer(decoding_method="greedy_search",**base),"greedy")
ents=[x for x in open(S/"dis_list.txt").read().split("\n") if x]|set() if False else sorted({x for x in open(S/"dis_list.txt").read().split("\n") if x}|{"arthur","saturday","elena"})
hw=S/"pk_hot_easy.txt"; hw.write_text("\n".join(f"{f} :1.0" for e in ents for f in {e.lower(),e.title()})+"\n")
h=score(so.OfflineRecognizer.from_transducer(decoding_method="modified_beam_search",max_active_paths=4,hotwords_file=str(hw),hotwords_score=1.0,modeling_unit="bpe",bpe_vocab=str(S/"pk.vocab"),**base),"mbs4 + 317-ish hotwords score=1.0")
for a,b in zip(g,h):
    if a!=b: print(" G:",a,"\n H:",b)
