import json,re,sys
sys.path.insert(0,"scripts")
import importlib.util
spec=importlib.util.spec_from_file_location("sc","scripts/stt_compare.py");sc=importlib.util.module_from_spec(spec);sys.modules["sc"]=sc;spec.loader.exec_module(sc)
res=json.load(open("out/stt_compare/results-hard.json"))
items={i["id"]:i for i in json.load(open("bench/stt_hard.json"))}
DIS="""taylor swift;the weeknd;drake;billie eilish;ariana grande;ed sheeran;dua lipa;bruno mars;olivia rodrigo;sabrina carpenter;kendrick lamar;doja cat;post malone;coldplay;rihanna;imagine dragons;bts;blackpink;sb19;ben and ben;moira dela torre;sarah geronimo;eraserheads;arthur nery;zack tabudlo;adie;juan karlos;bini;gloc 9;rivermaya;parokya ni edgar;kyla;regine velasquez;lea salonga;justin bieber;harry styles;lady gaga;adele;beyonce;eminem;shawn mendes;charlie puth;the chainsmokers;maroon 5;one direction;twice;newjeans;stray kids;blinding lights;espresso;shape of you;anti hero;levitating;golden;mundo;hawak kamay;opalite;tadhana;ere;pantropiko;die with a smile
jocelyn;angelo;mary grace;kristine;ezekiel reyes;jericho;ysabel dela cruz;paolo;bea;tita cora;joshua santos;maria clara;jose rizal;juan dela cruz;mark;nicole;patricia;gabriel;andrea;miguel;carlo;francis;rodrigo;liza;catherine;ronald;marites;lorna;tito boy;lola nena;kuya jun;ate mia
quezon city;makati;cebu;davao;tokyo;manila;pasig;marikina;baguio;iloilo;intramuros;taguig;mandaluyong;pasay;paranaque;las pinas;cagayan de oro;zamboanga;bonifacio global city;singapore;seoul;london;new york
jollibee;globe;gcash;shopee;grab;lazada;smart;mcdonalds;starbucks;mang inasal;netflix;spotify;youtube;facebook;messenger;viber;maya;foodpanda;uber;kfc""".replace("\n",";").split(";")
cat=set(DIS)|{e for i in items.values() for e in i["entities"]}
def key(s):  # crude phonetic skeleton
    s=s.lower(); s=re.sub(r"[^a-z0-9 ]","",s)
    for a,b in [("ph","f"),("ck","k"),("c(?=[eiy])","s"),("c","k"),("q","k"),("z","s"),("x","ks"),("y","i"),("w","u"),("v","b"),("j","h"),("tion","shon"),("sh","s"),("ch","s"),("th","t"),("gh",""),("(.)\\1","\\1")]:
        s=re.sub(a,b,s)
    s=re.sub(r"(?<=.)[aeiou]","",s) # drop non-initial vowels
    return s.replace(" ","")
def ed(a,b):
    d=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        p,d[0]=d[0],i
        for j,y in enumerate(b,1):
            p,d[j]=d[j],min(d[j]+1,d[j-1]+1,p+(x!=y))
    return d[-1]
K={e:key(e) for e in cat}
def correct(text,thr):
    w=sc.norm_words(text); out=[];i=0
    while i<len(w):
        best=None
        for n in (3,2,1):
            if i+n>len(w): continue
            seg=w[i:i+n]; k=key(" ".join(seg))
            if len(k)<3: continue
            for e,ke in K.items():
                if abs(len(k)-len(ke))>2: continue
                d=ed(k,ke)/max(len(k),len(ke))
                if d<=thr and (best is None or d<best[0] or (d==best[0] and n>best[2])): best=(d,e,n)
        if best and not (best[2]==1 and w[i]==best[1]): out+=sc.norm_words(best[1]); i+=best[2]
        else: out.append(w[i]); i+=1
    return " ".join(out)
for model in ("kroko","nemo-fc-480ms"):
    r=next(x for x in res if x["name"]==model)
    for thr in (None,0.2,0.3,0.4):
        hit=tot=edits=nw=0; cats={}
        for cid,kind,ref,hyp in r["rows"]:
            it=items[cid]; h=hyp if thr is None else correct(hyp,thr)
            hw=" "+" ".join(sc.norm_words(h))+" "
            for e in it["entities"]:
                ok=f" {' '.join(sc.norm_words(e))} " in hw
                c=cats.setdefault(it["kind"],[0,0]); c[0]+=ok; c[1]+=1; hit+=ok; tot+=1
            edits+=sc.word_edits(sc.norm_words(ref),sc.norm_words(h)); nw+=len(sc.norm_words(ref))
        print(f"{model:14} thr={thr}: entities {hit}/{tot}={hit/tot:.0%} WER {edits/nw:.3f}  "+" ".join(f"{k}:{v[0]}/{v[1]}" for k,v in cats.items()))
