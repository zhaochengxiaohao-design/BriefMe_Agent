"""HTTP client for accuracy report sources."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import requests
from openpyxl import load_workbook

from .dict import BINS, MATERIAL_LABEL
from config.settings import settings

logger = logging.getLogger(__name__)

ANALYSIS_LOGIN_PATH = "/auth/api/sysmgr/sso/login"
ANALYSIS_PAGING_PATH = "/module/mat-ana/analysisUi/biz/queryAnalysisPaging"
VISUAL_LOGIN_PATH_1 = "/furnace-particle-c3/api/record/doLogin"
VISUAL_LOGIN_PATH_2 = "/furnace-particle/api/record/doLogin"
VISUAL_PAGE_PATH_1 = "/furnace-particle-c3/api/batchRecord/page-condition"
VISUAL_PAGE_PATH_2 = "/furnace-particle/api/batchRecord/page-condition"


@dataclass
class HttpConfig:
    analysis_base_url: str = settings.yongfeng.analysis_base_url
    analysis_query_base_url: str = settings.yongfeng.analysis_query_base_url
    visual_1_base_url: str = settings.yongfeng.visual_1_base_url
    visual_2_base_url: str = settings.yongfeng.visual_2_base_url
    analysis_account: str = settings.yongfeng.account
    analysis_password: str = settings.yongfeng.password
    visual_account: str = settings.yongfeng.visual_username
    visual_password: str = settings.yongfeng.visual_password
    analysis_cookie: Optional[str] = None
    visual_1_cookie: Optional[str] = None
    visual_2_cookie: Optional[str] = None
    analysis_token: Optional[str] = None
    api_code: Optional[str] = settings.yongfeng.api_code
    timeout: int = 30


def _normalize_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    cleaned = str(token).strip()
    if not cleaned:
        return None
    if cleaned.lower().startswith("bearer "):
        cleaned = cleaned[7:].strip()
    elif cleaned.lower().startswith("bear "):
        cleaned = cleaned[5:].strip()
    if "%20" in cleaned:
        cleaned = cleaned.replace("%20", " ")
    return cleaned or None


def _clean_cookie_header(cookie: Optional[str]) -> Optional[str]:
    if not cookie:
        return None
    parts: List[str] = []
    for chunk in str(cookie).split(","):
        for item in chunk.split(";"):
            item = item.strip()
            if not item:
                continue
            low = item.lower()
            if low.startswith(("path=", "expires=", "max-age=", "httponly", "secure", "samesite=")):
                continue
            if "=" in item:
                k, v = item.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"')
                if k and v:
                    parts.append(f"{k}={v}")
    # 去重保序
    seen = set()
    cleaned = []
    for p in parts:
        if p not in seen:
            cleaned.append(p)
            seen.add(p)
    return "; ".join(cleaned) or None


def _build_headers(base_url: str, cookie: Optional[str] = None, token: Optional[str] = None, api_code: Optional[str] = None, referer_path: str = "/", x_server_origin: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": base_url.rstrip("/"),
        "Referer": base_url.rstrip("/") + referer_path,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0",
    }
    clean_cookie = _clean_cookie_header(cookie)
    if clean_cookie:
        headers["Cookie"] = clean_cookie
    if x_server_origin:
        headers["X-Server-Origin"] = x_server_origin
    normalized_token = _normalize_token(token)
    if normalized_token:
        headers["Authorization"] = normalized_token if normalized_token.startswith("Bearer ") else f"Bearer {normalized_token}"
    if api_code:
        headers["api-code"] = api_code
    return headers


def _request(method: str, url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int = 30) -> requests.Response:
    method = method.upper()
    if method == "GET":
        resp = requests.get(url, headers=headers, params=payload, timeout=timeout, verify=False)
    else:
        resp = requests.request(method, url, headers=headers, json=payload, timeout=timeout, verify=False)
    resp.raise_for_status()
    return resp


def _request_json(method: str, url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    resp = _request(method, url, headers, payload, timeout=timeout)
    text = (resp.text or "").strip()
    if not text:
        raise ValueError(f"{url} 返回空响应，status={resp.status_code}")
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "json" not in ctype and not text.startswith("{") and not text.startswith("["):
        preview = text[:300].replace("\n", " ")
        raise ValueError(f"{url} 返回非 JSON 响应: {preview}")
    try:
        return resp.json()
    except ValueError as exc:
        preview = text[:300].replace("\n", " ")
        raise ValueError(f"{url} 返回非 JSON 响应: {preview}") from exc


def _parse_dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S",):
        try:
            return datetime.strptime(str(v), fmt)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(str(v).replace(" ", "T"))
    except Exception:
        return None


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().rstrip("%"))
    except Exception:
        return None


def _extract_auth_from_response(resp: requests.Response, session: requests.Session) -> Dict[str, str]:
    cookie = resp.headers.get("Set-Cookie", "")
    if not cookie:
        cookie = "; ".join(f"{k}={v}" for k, v in session.cookies.get_dict().items())
    cookie = _clean_cookie_header(cookie) or ""
    token = resp.headers.get("Authorization") or resp.headers.get("authorization") or ""
    satoken = ""
    if not token:
        try:
            body = resp.json()
            token = body.get("authorization") or body.get("Authorization") or body.get("data", {}).get("authorization") or body.get("data", {}).get("Authorization") or ""
            data_val = body.get("data")
            if isinstance(data_val, str):
                satoken = data_val
            cookie = cookie or body.get("cookie") or body.get("data", {}).get("cookie") or ""
        except Exception:
            pass
    token = _normalize_token(token) or ""
    if not satoken and cookie:
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("satoken-undergroud="):
                satoken = part.split("=", 1)[1].strip().strip('"')
                break
    return {"cookie": cookie, "token": token, "satoken": satoken}


def _login_analysis(cfg: HttpConfig) -> Dict[str, str]:
    url = cfg.analysis_base_url.rstrip("/") + ANALYSIS_LOGIN_PATH
    headers = _build_headers(cfg.analysis_base_url, referer_path="/auth/")
    payload = {
        "captchaCode": "",
        "captchaId": "",
        "account": cfg.analysis_account,
        "domainNumber": "",
        "password": cfg.analysis_password,
    }
    session = requests.Session()
    session.headers.update(headers)
    session.get(cfg.analysis_base_url.rstrip("/") + "/auth/", timeout=cfg.timeout, verify=False)
    resp = session.post(url, headers={"Content-Type": "application/x-www-form-urlencoded"}, data=payload, timeout=cfg.timeout, verify=False)
    resp.raise_for_status()
    auth = _extract_auth_from_response(resp, session)
    logger.info("人工登录完成: cookie=%s token=%s", bool(auth.get("cookie")), bool(auth.get("token")))
    return auth


def _login_visual(cfg: HttpConfig, visual_idx: int) -> tuple[requests.Session, Dict[str, str]]:
    base_url = cfg.visual_1_base_url if visual_idx == 1 else cfg.visual_2_base_url
    login_path = VISUAL_LOGIN_PATH_1 if visual_idx == 1 else VISUAL_LOGIN_PATH_2
    url = base_url.rstrip("/") + login_path
    referer_path = "/furnace-particle-c3/" if visual_idx == 1 else "/furnace-particle/"
    headers = _build_headers(base_url, referer_path=referer_path, x_server_origin="http://127.0.0.1:28861")
    session = requests.Session()
    session.headers.update(headers)
    session.get(base_url.rstrip("/") + "/", timeout=cfg.timeout, verify=False)
    resp = session.get(url, params={"account": cfg.visual_account, "password": cfg.visual_password}, timeout=cfg.timeout, verify=False)
    resp.raise_for_status()
    auth = _extract_auth_from_response(resp, session)
    logger.info("视觉登录完成(%s): cookie=%s token=%s satoken=%s", visual_idx, bool(auth.get("cookie")), bool(auth.get("token")), bool(auth.get("satoken")))
    return session, auth


def fetch_raw_data(
    cfg: HttpConfig,
    mat_code_1: str,
    mat_code_2: str,
    start_time: str,
    end_time: str,
) -> Dict[str, Any]:
    analysis_login: Dict[str, str] = {}
    visual_login_1: Dict[str, str] = {}
    visual_login_2: Dict[str, str] = {}
    visual_session_1: Optional[requests.Session] = None
    visual_session_2: Optional[requests.Session] = None
    try:
        analysis_login = _login_analysis(cfg)
    except Exception as exc:
        logger.warning("人工登录失败，继续使用配置中已有 cookie/token: %s", exc)
        analysis_login = {"cookie": cfg.analysis_cookie or "", "token": cfg.analysis_token or ""}
    try:
        visual_session_1, visual_login_1 = _login_visual(cfg, 1)
    except Exception as exc:
        logger.warning("视觉1登录失败，继续使用配置中已有 cookie: %s", exc)
        visual_login_1 = {"cookie": cfg.visual_1_cookie or "", "token": "", "satoken": ""}
        visual_session_1 = None
    try:
        visual_session_2, visual_login_2 = _login_visual(cfg, 2)
    except Exception as exc:
        logger.warning("视觉2登录失败，继续使用配置中已有 cookie: %s", exc)
        visual_login_2 = {"cookie": cfg.visual_2_cookie or "", "token": "", "satoken": ""}
        visual_session_2 = None

    manual_1 = _fetch_manual(cfg, mat_code_1, start_time, end_time, auth=analysis_login)
    manual_2 = _fetch_manual(cfg, mat_code_2, start_time, end_time, auth=analysis_login)
    visual_1 = _fetch_visual(visual_session_1, cfg.visual_1_base_url, start_time, end_time, cfg.timeout, visual_idx=1, fallback_cookie=visual_login_1.get("cookie") or cfg.visual_1_cookie)
    visual_2 = _fetch_visual(visual_session_2, cfg.visual_2_base_url, start_time, end_time, cfg.timeout, visual_idx=2, fallback_cookie=visual_login_2.get("cookie") or cfg.visual_2_cookie)
    return {
        "meta": {
            "startTime": start_time,
            "endTime": end_time,
            "matCode1": mat_code_1,
            "matCode2": mat_code_2,
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
        },
        "manual_1": manual_1,
        "manual_2": manual_2,
        "visual_1": visual_1,
        "visual_2": visual_2,
        "login": {"analysis": analysis_login, "visual_1": visual_login_1, "visual_2": visual_login_2},
    }


def _fetch_manual(cfg: HttpConfig, mat_code: str, start_time: str, end_time: str, auth: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    payload = {
        "pubInfo": {"pageNum": 1, "pageSize": 9999999},
        "reqInfo": {"startTime": start_time, "endTime": end_time, "matCodeSet": [mat_code], "asc": False},
    }
    url = cfg.analysis_query_base_url.rstrip("/") + ANALYSIS_PAGING_PATH
    headers = _build_headers(cfg.analysis_query_base_url, cookie=(auth or {}).get("cookie") or cfg.analysis_cookie, token=(auth or {}).get("token") or cfg.analysis_token, api_code=cfg.api_code, referer_path="/fe-basic/?code=st2&theme=qbee-theme__default&qbee-language=zh&token=Bear%20")
    data = _request_json("POST", url, headers, payload, timeout=cfg.timeout)
    records = (((data.get("data") or {}).get("analysisItemValues") or {}).get("records") or [])
    rows: List[Dict[str, Any]] = []
    for rec in records:
        if str(rec.get("sampleSpotName") or "").strip() != MATERIAL_LABEL:
            continue
        if not _manual_is_qualified(rec):
            continue
        t = _parse_dt(rec.get("sampleTime"))
        if not t:
            continue
        vm = {str(x.get("name") or "").strip(): x for x in (rec.get("analysisValues") or [])}
        row = {"time": t}
        ok = True
        for b in BINS:
            val = _to_float((vm.get(b) or {}).get("value"))
            if val is None:
                ok = False
                break
            row[b] = val
        if ok:
            rows.append(row)
    return rows


def _manual_is_qualified(rec: Dict[str, Any]) -> bool:
    inspect_result = str(rec.get("inspectResult") or "").strip().upper()
    if inspect_result:
        return inspect_result == "Y"

    audit = str(rec.get("auditResult") or rec.get("auditStatus") or rec.get("reviewResult") or rec.get("verifyResult") or "").strip()
    if not audit:
        return True
    audit_low = audit.lower()
    if audit in {"不合格", "不通过", "NG", "ng", "n", "no"}:
        return False
    if any(token in audit_low for token in ["不合格", "reject", "fail", "invalid"]):
        return False
    return True


def _fetch_visual(session: Optional[requests.Session], visual_base_url: str, start_time: str, end_time: str, timeout: int, visual_idx: int, fallback_cookie: Optional[str] = None) -> List[Dict[str, Any]]:
    query_url = visual_base_url.rstrip("/") + (VISUAL_PAGE_PATH_1 if visual_idx == 1 else VISUAL_PAGE_PATH_2)
    referer_path = "/furnace-particle-c3/" if visual_idx == 1 else "/furnace-particle/"
    headers = _build_headers(visual_base_url, cookie=fallback_cookie, referer_path=referer_path, x_server_origin="http://127.0.0.1:28861")

    page_size = 15
    curr_page = 1
    rows: List[Dict[str, Any]] = []
    seen = set()

    while True:
        payload = {
            "currPage": curr_page,
            "pageSize": page_size,
            "startTime": start_time,
            "endTime": end_time,
            "orderList": [{"column": "id", "asc": False}],
            "particleConfig": "0,5,10,25,40",
        }
        logger.info("视觉查询请求(%s): url=%s payload=%s headers_cookie=%s", visual_idx, query_url, payload, bool(headers.get("Cookie")))
        if session is not None:
            resp = session.post(query_url, json=payload, timeout=timeout, verify=False)
        else:
            resp = requests.post(query_url, headers=headers, json=payload, timeout=timeout, verify=False)
        logger.info("视觉查询响应(%s): status=%s content_type=%s len=%s", visual_idx, resp.status_code, resp.headers.get("Content-Type"), len(resp.text or ""))
        preview = (resp.text or "")[:800].replace("\n", " ")
        logger.info("视觉查询响应预览(%s): %s", visual_idx, preview)
        resp.raise_for_status()
        data = resp.json()
        data_obj = data.get("data") if isinstance(data, dict) else None
        if isinstance(data_obj, dict):
            records = data_obj.get("records") or data_obj.get("list") or data_obj.get("rows") or []
            total_pages = int(data_obj.get("pages") or 0) if str(data_obj.get("pages") or "").isdigit() else 0
        elif isinstance(data_obj, list):
            records = data_obj
            total_pages = 1
        else:
            records = []
            total_pages = 0
        logger.info("视觉查询记录数(%s): %s", visual_idx, len(records))

        for rec in records:
            if not isinstance(rec, dict):
                continue
            statics_list = rec.get("staticsList")
            sub_records = [x for x in statics_list if isinstance(x, dict)] if isinstance(statics_list, list) and statics_list else [rec]
            for sub_rec in sub_records:
                t = _parse_dt(sub_rec.get("startTime")) or _parse_dt(sub_rec.get("endTime")) or _parse_dt(sub_rec.get("countTime")) or _parse_dt(sub_rec.get("gmtCreate")) or _parse_dt(sub_rec.get("time"))
                if not t:
                    continue
                t = t.replace(second=0, microsecond=0)
                dist_raw = sub_rec.get("distribution") or sub_rec.get("particleDistribution") or sub_rec.get("analysisValues") or []
                try:
                    dist = dist_raw if isinstance(dist_raw, list) else json.loads(dist_raw or "[]")
                except Exception:
                    dist = []
                row = {"time": t}
                if dist and isinstance(dist[0], dict):
                    for item in dist:
                        if not isinstance(item, dict):
                            continue
                        start = item.get("start")
                        end = item.get("end")
                        ratio = item.get("ratio")
                        if start == 0 and end == 5:
                            row["0-5mm"] = float(ratio) * 100 if ratio is not None else None
                        elif start == 5 and end == 10:
                            row["5-10mm"] = float(ratio) * 100 if ratio is not None else None
                        elif start == 10 and end == 25:
                            row["10-25mm"] = float(ratio) * 100 if ratio is not None else None
                        elif start == 25 and end == 40:
                            row["25-40mm"] = float(ratio) * 100 if ratio is not None else None
                        elif start == 40:
                            row[">40mm"] = float(ratio) * 100 if ratio is not None else None
                elif isinstance(dist, list) and dist and all(isinstance(x, (int, float)) for x in dist):
                    for idx, key in enumerate(BINS):
                        if idx < len(dist):
                            row[key] = float(dist[idx]) * 100
                if all(b in row and row[b] is not None for b in BINS) and t not in seen:
                    seen.add(t)
                    rows.append(row)

        if total_pages <= 0 or curr_page >= total_pages or not records:
            break
        curr_page += 1

    rows = sorted(rows, key=lambda x: x["time"])
    logger.info("视觉查询解析记录数(%s): %s", visual_idx, len(rows))
    return rows
