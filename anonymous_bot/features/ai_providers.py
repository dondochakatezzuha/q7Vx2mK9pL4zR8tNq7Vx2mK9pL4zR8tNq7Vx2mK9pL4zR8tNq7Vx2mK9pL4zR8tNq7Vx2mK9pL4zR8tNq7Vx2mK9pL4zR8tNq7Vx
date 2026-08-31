"""Multi-provider AI router with quota/rate-limit quarantine and Ollama fallback."""
import json, os, re, time, threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

_LOCK = threading.Lock()
_HEALTH = {}


def _valid(v):
    if not v: return False
    return v.strip().lower() not in {"none","null","your_key_here","put_your_api_key_here","put_your_gemini_api_key_here","put_your_groq_api_key_here","put_your_cerebras_api_key_here"}


def _parse_retry_after(text, headers=None):
    text = str(text or "")
    m = re.search(r"retry(?:-after| in)?[^0-9]{0,20}(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)", text, re.I)
    if m:
        n=float(m.group(1)); unit=m.group(2).lower()
        if unit.startswith("m") and not unit.startswith("ms"): n*=60
        elif unit.startswith("h"): n*=3600
        return max(5, min(int(n), 86400))
    if headers:
        v=headers.get("Retry-After") or headers.get("retry-after")
        try: return max(5, min(int(float(v)), 86400))
        except Exception: pass
    return None


def _cooldown(exc, status):
    text=str(exc)
    # Windows can temporarily reject outbound sockets with WinError 10013.
    # A short cooldown is enough; a long quarantine makes every provider look
    # permanently dead when the underlying network restriction clears.
    if "WINERROR 10013" in text.upper() or "[WINERROR 10013]" in text.upper() or "FORBIDDEN BY ITS ACCESS PERMISSIONS" in text.upper():
        return 30
    if status==402 or "PAYMENT REQUIRED" in text.upper(): return 21600
    retry=_parse_retry_after(text)
    if retry: return retry
    upper=text.upper()
    if any(x in upper for x in ("PER_DAY","TPD","RPD","DAILY QUOTA","REQUESTS_PER_DAY","GENERATE_REQUESTS_PER_DAY")):
        return 86400
    if status==429 or any(x in upper for x in ("RATE LIMIT","TOO MANY REQUESTS","RESOURCE_EXHAUSTED")):
        return 300
    if status in {500,502,503,504} or any(x in upper for x in ("TIMEOUT","TIMED OUT","UNAVAILABLE")):
        return 60
    return 300


def _quarantine(name, exc, status=None):
    seconds=_cooldown(exc,status)
    with _LOCK:
        previous=_HEALTH.get(name, {})
        until=time.time()+seconds
        _HEALTH[name]={"disabled_until":until,"reason":str(exc)[:500],"status":status}
        # Only print when a provider enters a new quarantine window.
        if previous.get("disabled_until",0) < time.time():
            mins=max(1, round(seconds/60))
            print(f"AI provider {name} quarantined for ~{mins}m: {str(exc)[:220]}")


def provider_status():
    now=time.time(); out={}
    with _LOCK:
        for name,h in _HEALTH.items():
            remaining=max(0,int(h.get("disabled_until",0)-now))
            out[name]={"available":remaining<=0,"cooldown_seconds":remaining,"reason":h.get("reason","")}
    return out


def _is_quarantined(name):
    with _LOCK:
        h=_HEALTH.get(name,{})
        if h.get("disabled_until",0) <= time.time():
            return False
        return True


def _request_json(url, headers, payload, timeout=45):
    data=json.dumps(payload).encode("utf-8")
    req=Request(url,data=data,headers={
        "Content-Type":"application/json",
        "Accept":"application/json",
        "User-Agent":"Anonymous-RPG-Bot/7",
        "Connection":"close",
        **headers,
    },method="POST")
    try:
        with urlopen(req,timeout=timeout) as r:
            raw=r.read().decode("utf-8",errors="replace")
            return json.loads(raw)
    except HTTPError as e:
        body=e.read().decode("utf-8",errors="replace")
        raise ProviderError(e.code,body,e.headers)
    except URLError as e:
        raise ProviderError(None,str(e),None)


class ProviderError(Exception):
    def __init__(self,status,body,headers=None):
        self.status=status; self.body=str(body); self.headers=headers
        super().__init__(f"HTTP {status}: {self.body[:500]}" if status else self.body[:500])


def _content(data):
    try:
        c=data["choices"][0]["message"].get("content") or ""
        if isinstance(c,list):
            return "".join(str(x.get("text",x)) for x in c if isinstance(x,dict))
        return str(c)
    except Exception:
        return str(data.get("response") or data.get("result",{}).get("response") or "")


def _openai_call(name, key, base, model, prompt, system, headers=None, max_tokens=900):
    payload={"model":model,"messages":[{"role":"system","content":system},{"role":"user","content":prompt}],"temperature":0.2,"max_tokens":max_tokens,"stream":False}
    h={"Authorization":f"Bearer {key}"}
    if headers: h.update(headers)
    data=_request_json(base.rstrip("/")+"/chat/completions",h,payload)
    text=_content(data).strip()
    if not text: raise RuntimeError(f"{name}: empty response")
    return text


def _ollama_call(model,prompt,system,max_tokens=900):
    base=os.getenv("OLLAMA_BASE_URL","http://127.0.0.1:11434").rstrip("/")
    payload={"model":model,"messages":[{"role":"system","content":system},{"role":"user","content":prompt}],"stream":False,"options":{"temperature":0.2,"num_predict":max_tokens}}
    data=_request_json(base+"/api/chat",{},payload,timeout=120)
    text=(data.get("message") or {}).get("content","").strip()
    if not text: raise RuntimeError("Ollama: empty response")
    return text


def _gemini_call(model,key,prompt,system,max_tokens=900):
    # Use the Gemini Chat API rather than Models.generate_content directly.
    # This avoids the SDK's automatic-function-calling warning and keeps the
    # provider path compatible with normal multi-turn chat semantics.
    from google import genai
    client=genai.Client(api_key=key)
    try:
        chat=client.chats.create(
            model=model,
            config={
                "system_instruction": system,
                "temperature": 0.2,
                "max_output_tokens": max_tokens,
            },
        )
        response=chat.send_message(prompt)
    except TypeError:
        # Older google-genai versions may require the config object shape from
        # the installed SDK. Fall back to the direct call without AFC.
        response=client.models.generate_content(
            model=model,
            contents=system+"\n\n"+prompt,
            config={"temperature":0.2,"max_output_tokens":max_tokens},
        )
    text=getattr(response,"text","") or ""
    if not text.strip(): raise RuntimeError("Gemini: empty response")
    return text.strip()


def _providers():
    return [
      ("groq", os.getenv("GROQ_API_KEY"), "https://api.groq.com/openai/v1", os.getenv("GROQ_MODEL","llama-3.3-70b-versatile")),
      ("gemini", os.getenv("GEMINI_API_KEY"), "", os.getenv("GEMINI_MODEL","gemini-3.7-flash")),
      ("cerebras", os.getenv("CEREBRAS_API_KEY"), "https://api.cerebras.ai/v1", os.getenv("CEREBRAS_MODEL","gpt-oss-120b")),
      ("mistral", os.getenv("MISTRAL_API_KEY"), "https://api.mistral.ai/v1", os.getenv("MISTRAL_MODEL","mistral-small-latest")),
      ("sambanova", os.getenv("SAMBANOVA_API_KEY"), "https://api.sambanova.ai/v1", os.getenv("SAMBANOVA_MODEL","Meta-Llama-3.3-70B-Instruct")),
      ("openrouter", os.getenv("OPENROUTER_API_KEY"), "https://openrouter.ai/api/v1", os.getenv("OPENROUTER_MODEL","openrouter/free")),
      ("github", os.getenv("GITHUB_TOKEN"), "https://models.github.ai/inference", os.getenv("GITHUB_MODEL","openai/gpt-4.1-mini")),
      ("nvidia", os.getenv("NVIDIA_API_KEY"), "https://integrate.api.nvidia.com/v1", os.getenv("NVIDIA_MODEL","nvidia/llama-3.3-nemotron-super-49b-v1")),
      ("huggingface", os.getenv("HUGGINGFACE_API_KEY"), "https://router.huggingface.co/v1", os.getenv("HUGGINGFACE_MODEL","meta-llama/Llama-3.1-8B-Instruct")),
      ("chutes", os.getenv("CHUTES_API_KEY"), "https://llm.chutes.ai/v1", os.getenv("CHUTES_MODEL","Qwen/Qwen3-32B-TEE")),
      ("pollinations", os.getenv("POLLINATIONS_API_KEY"), "https://gen.pollinations.ai/v1", os.getenv("POLLINATIONS_MODEL","openai")),
      ("llm7", os.getenv("LLM7_API_KEY"), "https://api.llm7.io/v1", os.getenv("LLM7_MODEL","default")),
      ("ollama", "local", "", os.getenv("OLLAMA_MODEL","llama3.2:3b")),
    ]


def _configured(name,key):
    if name=="ollama": return True
    return _valid(key)


def _ordered():
    pref=os.getenv("AI_PROVIDER","auto").strip().lower()
    ps=[x for x in _providers() if _configured(x[0],x[1])]
    if pref and pref!="auto":
        ps.sort(key=lambda x: 0 if x[0]==pref else 1)
    return ps


def _call(name,key,base,model,prompt,system,max_tokens):
    if name=="ollama": return _ollama_call(model,prompt,system,max_tokens)
    if name=="gemini": return _gemini_call(model,key,prompt,system,max_tokens)
    return _openai_call(name,key,base,model,prompt,system,max_tokens=max_tokens)


def _groq_call_with_fallback(key, model, prompt, system, max_tokens):
    # Some Groq projects/keys can return model_not_found even though the model
    # exists globally (for example because project/model access differs). Try
    # the smaller production model before giving up on the provider.
    try:
        return _openai_call("groq", key, "https://api.groq.com/openai/v1", model, prompt, system, max_tokens=max_tokens)
    except ProviderError as exc:
        body=exc.body.upper()
        if exc.status == 404 and "MODEL_NOT_FOUND" in body:
            fallback=os.getenv("GROQ_FALLBACK_MODEL", "llama-3.1-8b-instant")
            if fallback and fallback != model:
                return _openai_call("groq", key, "https://api.groq.com/openai/v1", fallback, prompt, system, max_tokens=max_tokens)
        raise


def complete(prompt, structured=False, max_tokens=1000):
    system=("Return only the JSON requested by the user. Do not add markdown fences or commentary." if structured else "Answer in clear, concise prose. Do not return JSON unless explicitly requested.")
    errors=[]
    for name,key,base,model in _ordered():
        if _is_quarantined(name):
            continue
        try:
            if name == "groq":
                result=_groq_call_with_fallback(key, model, prompt, system, max_tokens)
            else:
                result=_call(name,key,base,model,prompt,system,max_tokens)
            if result.strip(): return result
            raise RuntimeError("empty response")
        except ProviderError as exc:
            _quarantine(name,exc,exc.status)
            errors.append(f"{name.title()}: HTTP {exc.status}")
        except Exception as exc:
            status=getattr(exc,"status",None)
            # SDK errors often expose status_code instead.
            status=status or getattr(exc,"status_code",None)
            if status or any(x in str(exc).upper() for x in ("429","RESOURCE_EXHAUSTED","RATE LIMIT","TOO MANY REQUESTS","TPD","RPD","PAYMENT REQUIRED"," 402")):
                _quarantine(name,exc,status)
            else:
                # Provider is temporarily unhealthy; quarantine briefly to avoid spam.
                _quarantine(name,exc,status)
            errors.append(f"{name.title()}: {type(exc).__name__}")
    raise RuntimeError("All configured AI providers failed. " + " | ".join(errors))
