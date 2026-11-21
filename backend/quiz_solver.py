import json
import logging
import threading
import time
import re
from typing import List, Dict, Any, Optional, Tuple, Set
import requests
from requests.adapters import HTTPAdapter
import openai
from openai import OpenAI
from backend.config import settings

try:
    import httpx2  # bundled by openai>=3.x
except ImportError:
    httpx2 = None  # type: ignore

logger = logging.getLogger(__name__)

class AISolverError(Exception):
    """Raised when the AI API fails or response cannot be reliably parsed."""
    pass

SYSTEM_PROMPT = """You are an expert Islamic Scholar and trivia master specializing in Islamic knowledge, Seerah of the Prophet Muhammad ﷺ, the Holy Quran, and the history and landmarks of Makkah and Madinah.
You are playing the Nusuk Challenge quiz on the official Saudi Nusuk app.

Instructions:
1. You will be provided with the full question text and an ordered list of options (indexed 0 to N-1).
2. Analyze the question carefully, paying attention to specific Arabic / Islamic terms and honorifics.
3. Select EXACTLY ONE option from the provided list that is the single best and most correct answer.
4. You MUST respond ONLY in valid JSON matching this schema:
   {
       "selected_index": <int, 0-based index of your chosen option>,
       "selected_answer": "<exact text of your chosen option>",
       "reasoning": "<brief 1-sentence reason>"
   }
No markdown backticks, no extra text outside the JSON.
"""

def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """Robust extractor that handles raw JSON, markdown-wrapped JSON, or embedded JSON."""
    if not text:
        return None
    # 1. Direct parse
    try:
        return json.loads(text.strip())
    except Exception:
        pass

    # 2. Extract from markdown code blocks ```json ... ``` or ``` ... ```
    md_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if md_match:
        try:
            return json.loads(md_match.group(1).strip())
        except Exception:
            pass

    # 3. Search for outermost { ... }
    brace_match = re.search(r"(\{[\s\S]*\})", text)
    if brace_match:
        try:
            return json.loads(brace_match.group(1).strip())
        except Exception:
            pass

    return None

def normalize_ai_endpoint(base_url: Optional[str]) -> Tuple[str, str]:
    """
    Normalizes AI base URL and determines endpoint type.
    Returns: (normalized_url, endpoint_type)
    Endpoint types:
      - 'gemini_interactions': Google Gemini Interactions API
      - 'gemini_generate_content': Google Gemini native generateContent API
      - 'openai_compatible': Standard OpenAI-compatible API
    """
    raw = (base_url or "https://api.openai.com/v1").strip()
    
    # 1. Detect Gemini Interactions API
    if "/interactions" in raw:
        # Strip trailing slashes
        clean_url = raw.rstrip("/")
        return clean_url, "gemini_interactions"

    # 2. Detect Gemini native generateContent API
    if ":generateContent" in raw or ":streamGenerateContent" in raw:
        clean_url = raw.rstrip("/")
        return clean_url, "gemini_generate_content"

    # 3. Strip trailing /chat/completions if user accidentally included full path
    # e.g. https://.../v1/chat/completions -> https://.../v1
    clean_url = re.sub(r"/chat/completions/?$", "", raw, flags=re.IGNORECASE).rstrip("/")

    # 4. Smart Google Gemini auto-redirection to OpenAI-compatible endpoint
    # If host is generativelanguage.googleapis.com and lacks /openai/ or specific endpoint
    if "generativelanguage.googleapis.com" in clean_url:
        if "/openai" not in clean_url:
            # Map root or /v1beta or /v1 to /v1beta/openai
            clean_url = "https://generativelanguage.googleapis.com/v1beta/openai"

    return clean_url, "openai_compatible"

class QuizSolver:
    def __init__(self):
        self._pool_lock = threading.Lock()
        self._openai_clients: Dict[Tuple[str, str], OpenAI] = {}
        self._json_mode_unsupported: Set[Tuple[str, str]] = set()
        self._requests_session = self._build_requests_session()

    @staticmethod
    def _build_requests_session() -> requests.Session:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=16, pool_maxsize=32)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _create_openai_client(self, norm_url: str, key: str) -> OpenAI:
        default_http_cls = getattr(openai, "DefaultHttpxClient", None)
        if default_http_cls is not None and httpx2 is not None and hasattr(httpx2, "Limits"):
            try:
                http_client = default_http_cls(
                    limits=httpx2.Limits(
                        max_connections=32,
                        max_keepalive_connections=16,
                        keepalive_expiry=60.0,
                    )
                )
                return OpenAI(base_url=norm_url, api_key=key, http_client=http_client)
            except Exception as e:
                logger.debug(f"Falling back to default OpenAI HTTP client: {e}")
        return OpenAI(base_url=norm_url, api_key=key)

    def get_openai_client(self, base_url: Optional[str] = None, api_key: Optional[str] = None) -> OpenAI:
        """Returns a thread-safe, keep-alive pooled OpenAI client keyed by (normalized_url, api_key)."""
        norm_url, _ = normalize_ai_endpoint(base_url or settings.ai_base_url)
        key = api_key or settings.ai_api_key or "dummy-key"
        cache_key = (norm_url, key)
        with self._pool_lock:
            client = self._openai_clients.get(cache_key)
            if client is None:
                # Cap cached clients to prevent unbounded growth if credentials are changed repeatedly
                if len(self._openai_clients) >= 8:
                    oldest_key = next(iter(self._openai_clients))
                    old_client = self._openai_clients.pop(oldest_key, None)
                    try:
                        if old_client is not None:
                            old_client.close()
                    except Exception:
                        pass
                client = self._create_openai_client(norm_url, key)
                self._openai_clients[cache_key] = client
            return client

    def test_connection(self, base_url: Optional[str] = None, api_key: Optional[str] = None, model: Optional[str] = None) -> Dict[str, Any]:
        """Tests whether the configured AI endpoint, key, and model respond properly."""
        target_url = base_url or settings.ai_base_url or "https://api.openai.com/v1"
        target_key = api_key or settings.ai_api_key or ""
        target_model = model or settings.ai_model or "gpt-4o-mini"

        norm_url, ep_type = normalize_ai_endpoint(target_url)

        if not target_key:
            return {
                "success": False,
                "error": "API Key kosong. Silakan masukkan AI API Key yang valid.",
                "model": target_model,
                "target_url": norm_url,
                "endpoint_type": ep_type
            }

        # --- BRANCH A: Gemini Interactions ---
        if ep_type == "gemini_interactions":
            try:
                headers = {
                    "Content-Type": "application/json",
                    "x-goog-api-key": target_key
                }
                body = {
                    "model": target_model,
                    "input": "Respond strictly with JSON: {\"status\": \"ok\"}"
                }
                resp = self._requests_session.post(norm_url, headers=headers, json=body, timeout=12.0)
                if resp.status_code == 200:
                    return {
                        "success": True,
                        "model": target_model,
                        "raw_response": resp.text[:200],
                        "mode": "gemini_interactions",
                        "target_url": norm_url
                    }
                else:
                    return {
                        "success": False,
                        "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
                        "model": target_model,
                        "target_url": norm_url,
                        "endpoint_type": ep_type
                    }
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Connection error to Gemini Interactions ({norm_url}): {str(e)}",
                    "model": target_model,
                    "target_url": norm_url,
                    "endpoint_type": ep_type
                }

        # --- BRANCH B: Gemini Native generateContent ---
        if ep_type == "gemini_generate_content":
            try:
                # If target_model placeholder is needed
                req_url = norm_url
                if "{model}" in req_url:
                    req_url = req_url.replace("{model}", target_model)
                elif ":generateContent" in req_url and "models/" not in req_url:
                    # insert model
                    req_url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent"

                headers = {
                    "Content-Type": "application/json",
                    "x-goog-api-key": target_key
                }
                body = {
                    "contents": [{"parts": [{"text": "Respond with JSON: {\"status\": \"ok\"}"}]}],
                    "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"}
                }
                resp = self._requests_session.post(req_url, headers=headers, json=body, timeout=12.0)
                if resp.status_code == 200:
                    return {
                        "success": True,
                        "model": target_model,
                        "raw_response": resp.text[:200],
                        "mode": "gemini_generate_content",
                        "target_url": req_url
                    }
                else:
                    return {
                        "success": False,
                        "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
                        "model": target_model,
                        "target_url": req_url,
                        "endpoint_type": ep_type
                    }
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Connection error to Gemini generateContent ({norm_url}): {str(e)}",
                    "model": target_model,
                    "target_url": norm_url,
                    "endpoint_type": ep_type
                }

        # --- BRANCH C: Standard OpenAI-Compatible (Includes Gemini OpenAI Mode) ---
        client = self.get_openai_client(base_url=norm_url, api_key=target_key)
        test_messages = [
            {"role": "system", "content": "You are a helpful assistant. Respond strictly with JSON: {\"status\": \"ok\"}"},
            {"role": "user", "content": "ping"}
        ]

        # 1. Try with response_format json_object
        try:
            resp = client.chat.completions.create(
                model=target_model,
                messages=test_messages,
                response_format={"type": "json_object"},
                timeout=12.0
            )
            raw = resp.choices[0].message.content
            with self._pool_lock:
                self._json_mode_unsupported.discard((norm_url, target_model))
            return {
                "success": True,
                "model": target_model,
                "raw_response": raw,
                "mode": "json_object",
                "target_url": norm_url
            }
        except Exception as e1:
            err_str = str(e1)
            # Check if error is specifically about json_object not supported
            if "response_format" in err_str.lower() or "json_object" in err_str.lower() or "400" in err_str:
                try:
                    resp = client.chat.completions.create(
                        model=target_model,
                        messages=test_messages,
                        timeout=12.0
                    )
                    raw = resp.choices[0].message.content
                    with self._pool_lock:
                        self._json_mode_unsupported.add((norm_url, target_model))
                    return {
                        "success": True,
                        "model": target_model,
                        "raw_response": raw,
                        "mode": "standard_fallback",
                        "target_url": norm_url
                    }
                except Exception as e2:
                    return {
                        "success": False,
                        "error": self._format_error(str(e2), norm_url, target_model),
                        "model": target_model,
                        "target_url": norm_url,
                        "endpoint_type": ep_type
                    }

            return {
                "success": False,
                "error": self._format_error(err_str, norm_url, target_model),
                "model": target_model,
                "target_url": norm_url,
                "endpoint_type": ep_type
            }

    def _format_error(self, err: str, url: str, model: str) -> str:
        if "404" in err:
            return f"HTTP 404 (Not Found): URL '{url}' atau model '{model}' tidak ditemukan. Jika memakai Gemini OpenAI-compatible, pastikan URL berakhiran '/v1beta/openai'."
        if "401" in err or "403" in err:
            return f"HTTP 401/403 (Unauthorized): API Key tidak valid atau tidak memiliki izin akses ke model '{model}'."
        if "429" in err:
            return f"HTTP 429 (Rate Limit): Kuota API atau rate limit terlampaui pada provider."
        return err

    def solve_quiz(self, question: str, options: List[str], max_retries: int = 2) -> Dict[str, Any]:
        """
        Thread-safe entry point: measures cumulative AI API time (incl. retries) as `ai_duration_ms`.
        Uses persistent thread-safe HTTP keep-alive connection pools across concurrent workers.
        """
        t0 = time.perf_counter()
        try:
            result = self._solve_quiz_impl(question, options, max_retries)
        except AISolverError as e:
            e.ai_duration_ms = int((time.perf_counter() - t0) * 1000)
            raise
        result["ai_duration_ms"] = int((time.perf_counter() - t0) * 1000)
        return result

    def _solve_quiz_impl(self, question: str, options: List[str], max_retries: int = 2) -> Dict[str, Any]:
        """
        Sends the question and options to the configured AI API.
        CRITICAL: Never fall back to index 0 or random guess on failure.
        Always raises AISolverError if AI fails or answer cannot be resolved.
        """
        api_key = settings.ai_api_key
        if not api_key:
            raise AISolverError("AI API Key tidak dikonfigurasi di Settings. Bot dihentikan demi keamanan.")

        norm_url, ep_type = normalize_ai_endpoint(settings.ai_base_url)
        current_model = settings.ai_model or "gpt-4o-mini"
        user_content = {
            "question": question,
            "options": [f"[{i}] {opt}" for i, opt in enumerate(options)]
        }
        prompt_text = json.dumps(user_content, ensure_ascii=False)

        delay = 0.5
        last_error = ""

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Solving quiz via AI ({current_model}) on {norm_url} (type: {ep_type}), attempt {attempt}...")
                raw_text = ""

                # --- CALL ROUTING ---
                if ep_type == "gemini_interactions":
                    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
                    body = {
                        "model": current_model,
                        "input": [
                            {"role": "system", "parts": [{"text": SYSTEM_PROMPT}]},
                            {"role": "user", "parts": [{"text": prompt_text}]}
                        ]
                    }
                    resp = self._requests_session.post(norm_url, headers=headers, json=body, timeout=15.0)
                    if resp.status_code != 200:
                        raise AISolverError(f"Gemini Interactions error HTTP {resp.status_code}: {resp.text[:300]}")
                    resp_json = resp.json()
                    # Interactions return format
                    raw_text = resp_json.get("output", "") or str(resp_json)

                elif ep_type == "gemini_generate_content":
                    req_url = norm_url
                    if "{model}" in req_url:
                        req_url = req_url.replace("{model}", current_model)
                    elif ":generateContent" in req_url and "models/" not in req_url:
                        req_url = f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:generateContent"

                    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
                    body = {
                        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                        "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
                        "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"}
                    }
                    resp = self._requests_session.post(req_url, headers=headers, json=body, timeout=15.0)
                    if resp.status_code != 200:
                        raise AISolverError(f"Gemini generateContent error HTTP {resp.status_code}: {resp.text[:300]}")
                    resp_json = resp.json()
                    try:
                        raw_text = resp_json["candidates"][0]["content"]["parts"][0]["text"]
                    except Exception:
                        raw_text = str(resp_json)

                else:
                    # OpenAI-Compatible (persistent keep-alive pooled client)
                    client = self.get_openai_client(base_url=norm_url, api_key=api_key)
                    messages = [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt_text}
                    ]
                    model_key = (norm_url, current_model)
                    with self._pool_lock:
                        skip_json_mode = model_key in self._json_mode_unsupported

                    if skip_json_mode:
                        response = client.chat.completions.create(
                            model=current_model,
                            temperature=0.0,
                            messages=messages,
                            timeout=15.0
                        )
                    else:
                        try:
                            response = client.chat.completions.create(
                                model=current_model,
                                temperature=0.0,
                                response_format={"type": "json_object"},
                                messages=messages,
                                timeout=15.0
                            )
                        except Exception as json_err:
                            err_str = str(json_err).lower()
                            if "response_format" in err_str or "json_object" in err_str or "400" in err_str:
                                with self._pool_lock:
                                    self._json_mode_unsupported.add(model_key)
                                response = client.chat.completions.create(
                                    model=current_model,
                                    temperature=0.0,
                                    messages=messages,
                                    timeout=15.0
                                )
                            else:
                                raise json_err

                    raw_text = response.choices[0].message.content or ""

                # --- PARSE AND VALIDATE RESULT ---
                data = extract_json_from_text(raw_text)
                if data and "selected_index" in data:
                    try:
                        idx = int(data.get("selected_index"))
                    except (ValueError, TypeError):
                        idx = -1

                    if 0 <= idx < len(options):
                        return {
                            "selected_index": idx,
                            "selected_answer": options[idx],
                            "reasoning": data.get("reasoning", "")
                        }

                    # If index out of bounds, try matching selected_answer string
                    selected_ans = str(data.get("selected_answer", "")).strip().lower()
                    if selected_ans:
                        for i, opt in enumerate(options):
                            opt_clean = opt.strip().lower()
                            if selected_ans in opt_clean or opt_clean in selected_ans:
                                return {
                                    "selected_index": i,
                                    "selected_answer": options[i],
                                    "reasoning": f"Matched by answer text: {data.get('reasoning', '')}"
                                }

                # If JSON didn't give a valid index, try finding option text inside raw response
                for i, opt in enumerate(options):
                    if opt.strip().lower() in raw_text.lower():
                        return {
                            "selected_index": i,
                            "selected_answer": options[i],
                            "reasoning": "Direct option text found in AI output"
                        }

                # If we reached here, AI returned unparsable output
                raise AISolverError(f"AI response could not be mapped to any option. Raw response: {raw_text[:200]}")

            except AISolverError:
                raise
            except Exception as e:
                last_error = str(e)
                logger.warning(f"AI solver attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise AISolverError(f"Panggilan ke AI API gagal setelah {max_retries} percobaan: {last_error}")

        raise AISolverError(f"AI solver exhausted all attempts without valid answer. Error: {last_error}")

quiz_solver = QuizSolver()
