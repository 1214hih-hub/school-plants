import base64
import io
import json
import time

import requests
import streamlit as st
from PIL import Image, ImageOps

st.set_page_config(page_title="우리 학교 식물 도감", page_icon="🌿")

DEFAULT_MODEL = "gemini-2.5-flash"

PROMPT = (
    "이 사진 속 식물이 무엇인지 알려 주세요. 초·중학생이 읽을 수 있게 쉬운 한국어로 답하세요. "
    "가능성이 높은 순서대로 후보를 최대 3개 제시하세요. "
    "각 후보에는 다음을 넣으세요: "
    "korean_name(한국어 이름, 모르면 빈 문자열), scientific_name(학명), family(과 이름, 한국어), "
    "confidence(0에서 100 사이 정수, 당신이 생각하는 가능성), "
    "description(생김새, 꽃과 열매, 자라는 환경 등 특징을 3~4문장). "
    "확실하지 않은 내용은 지어내지 말고 생략하세요. "
    "사진에 식물이 없으면 candidates를 빈 배열로 답하세요."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "korean_name": {"type": "string"},
                    "scientific_name": {"type": "string"},
                    "family": {"type": "string"},
                    "confidence": {"type": "integer"},
                    "description": {"type": "string"},
                },
                "required": ["korean_name", "scientific_name", "confidence", "description"],
            },
        }
    },
    "required": ["candidates"],
}


def secret(name):
    try:
        return str(st.secrets[name]).strip()
    except Exception:
        return ""


def prepare_image(raw):
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((1280, 1280))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def call_gemini(jpeg, key, model, use_schema=True):
    gen_config = {"responseMimeType": "application/json", "temperature": 0.2}
    if use_schema:
        gen_config["responseSchema"] = SCHEMA
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": PROMPT},
                    {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(jpeg).decode()}},
                ]
            }
        ],
        "generationConfig": gen_config,
    }
    url = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % model
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    last_error = ""
    r = None
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=(15, 90))
        except requests.exceptions.RequestException as e:
            last_error = type(e).__name__
            time.sleep(3)
            continue
        if r.status_code in (429, 500, 503):
            last_error = "HTTP %s" % r.status_code
            time.sleep(4)
            continue
        return r
    if r is not None and last_error.startswith("HTTP"):
        return r
    raise RuntimeError("Gemini 서버에 연결하지 못했습니다 (%s). 잠시 후 다시 시도하세요." % last_error)


def parse_candidates(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    data = json.loads(text)
    if isinstance(data, list):
        items = data
    else:
        items = data.get("candidates", [])
    out = []
    for item in items[:3]:
        try:
            score = int(round(float(item.get("confidence", 0))))
        except Exception:
            score = 0
        out.append(
            {
                "score": max(0, min(100, score)),
                "sci": str(item.get("scientific_name", "") or "").strip(),
                "common": str(item.get("korean_name", "") or "").strip(),
                "family": str(item.get("family", "") or "").strip(),
                "desc": str(item.get("description", "") or "").strip(),
            }
        )
    return out


def identify(jpeg, key, model):
    r = call_gemini(jpeg, key, model, use_schema=True)
    if r.status_code == 400:
        r = call_gemini(jpeg, key, model, use_schema=False)
    if r.status_code == 404:
        raise RuntimeError("Gemini 모델 이름(%s)을 찾을 수 없습니다. 선생님께 알려 주세요." % model)
    if r.status_code in (401, 403):
        raise RuntimeError("Gemini API 키가 올바르지 않거나 권한이 없습니다 (%s). 선생님께 알려 주세요." % r.status_code)
    if r.status_code == 429:
        raise RuntimeError("Gemini 사용량 한도에 걸렸습니다 (429). 잠시 후 다시 시도하세요.")
    if r.status_code != 200:
        raise RuntimeError("Gemini 오류 (%s): %s" % (r.status_code, r.text[:200]))
    try:
        cand = r.json()["candidates"][0]
        text = "".join(p.get("text", "") for p in cand["content"]["parts"])
    except Exception:
        raise RuntimeError("Gemini가 답을 주지 않았습니다. 사진을 바꿔서 다시 시도하세요.")
    try:
        return parse_candidates(text)
    except Exception:
        raise RuntimeError("Gemini 답변을 읽지 못했습니다. 다시 시도하세요.")


def upload_image(jpeg, key):
    r = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": key, "image": base64.b64encode(jpeg).decode()},
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError("imgbb 오류 (%s): %s" % (r.status_code, r.text[:200]))
    return r.json()["data"]["url"]


def post_to_padlet(subject, body, image_url, api_key, board_id):
    payload = {
        "data": {
            "type": "post",
            "attributes": {
                "content": {
                    "subject": subject,
                    "body": body,
                    "attachment": {"url": image_url},
                }
            },
        }
    }
    r = requests.post(
        "https://api.padlet.dev/v1/boards/%s/posts" % board_id,
        json=payload,
        headers={
            "X-Api-Key": api_key,
            "accept": "application/vnd.api+json",
            "content-type": "application/vnd.api+json",
        },
        timeout=60,
    )
    if r.status_code >= 300:
        raise RuntimeError("Padlet 오류 (%s): %s" % (r.status_code, r.text[:300]))


st.title("🌿 우리 학교 식물 도감")
st.caption("식물 사진을 찍고 [검색하기]를 누르면 AI가 이름과 특징을 알려 줍니다.")

class_code = secret("CLASS_CODE")
if class_code:
    typed = st.text_input("수업 코드", type="password")
    if typed != class_code:
        st.info("선생님이 알려 준 수업 코드를 입력하세요.")
        st.stop()

student = st.text_input("이름 (별명이나 번호도 좋아요)")
place = st.text_input("발견한 장소 (예: 운동장 왼쪽 화단)")

tab1, tab2 = st.tabs(["바로 촬영", "앨범에서 선택"])
with tab1:
    shot = st.camera_input("식물을 촬영하세요")
with tab2:
    uploaded = st.file_uploader("사진 선택", type=["jpg", "jpeg", "png"])

photo = shot or uploaded

if st.button("🔍 검색하기", type="primary"):
    if photo is None:
        st.warning("먼저 사진을 찍거나 선택하세요.")
    elif not student.strip():
        st.warning("이름을 입력하세요.")
    else:
        key = secret("GEMINI_API_KEY")
        model = secret("GEMINI_MODEL") or DEFAULT_MODEL
        if not key:
            st.error("Gemini API 키가 설정되지 않았습니다. 선생님께 알려 주세요.")
        else:
            try:
                jpeg = prepare_image(photo.getvalue())
                with st.spinner("식물을 찾는 중입니다..."):
                    cands = identify(jpeg, key, model)
                st.session_state["jpeg"] = jpeg
                st.session_state["cands"] = cands
                st.session_state["done"] = False
            except RuntimeError as e:
                st.error("검색에 실패했습니다: %s" % e)
            except Exception as e:
                st.error("검색에 실패했습니다 (%s). 잠시 후 다시 시도하세요." % type(e).__name__)

cands = st.session_state.get("cands")
if cands is not None:
    st.image(st.session_state["jpeg"], caption="내가 찍은 사진", use_container_width=True)
    if not cands:
        st.warning("식물을 찾지 못했습니다. 잎이나 꽃이 잘 보이게 가까이에서 다시 찍어 보세요.")
    else:
        st.subheader("후보 식물")
        st.write("AI가 추정한 결과라 틀릴 수 있습니다. 실제 식물과 비교해서 가장 비슷한 것을 고르세요.")
        labels = []
        for c in cands:
            labels.append("%s (%s) - AI 추정 %s%%" % (c["common"] or "한국어 이름 없음", c["sci"], c["score"]))
        idx = st.radio("후보 선택", range(len(cands)), format_func=lambda i: labels[i])
        c = cands[idx]

        name = st.text_input("식물 이름", value=c["common"] or c["sci"], key="name_%d" % idx)
        desc = st.text_area(
            "특징 (AI가 쓴 설명이라 틀릴 수 있어요. 고쳐 써도 됩니다)",
            value=c["desc"] or "설명이 없습니다. 직접 써 보세요.",
            height=180,
            key="desc_%d" % idx,
        )
        obs = st.text_area("내가 관찰한 점 (잎 모양, 꽃 색깔, 냄새 등)", key="obs_%d" % idx)

        if st.button("📌 Padlet에 게시하기", disabled=st.session_state.get("done", False)):
            lines = [
                "학명: %s" % (c["sci"] or "알 수 없음"),
                "과(科): %s" % (c["family"] or "알 수 없음"),
                "AI 추정 가능성: %s%%" % c["score"],
                "",
                "[특징] (AI 설명)",
                desc,
            ]
            if obs.strip():
                lines += ["", "[내가 관찰한 점]", obs.strip()]
            lines += ["", "발견 장소: %s" % (place or "적지 않음"), "올린 사람: %s" % student]
            body = "\n".join(lines)

            padlet_key = secret("PADLET_API_KEY")
            board_id = secret("PADLET_BOARD_ID")
            imgbb_key = secret("IMGBB_API_KEY")
            try:
                with st.spinner("게시하는 중입니다..."):
                    image_url = upload_image(st.session_state["jpeg"], imgbb_key)
                    post_to_padlet(name, body, image_url, padlet_key, board_id)
                st.success("Padlet에 게시했습니다! 🎉")
                st.session_state["done"] = True
            except Exception as e:
                st.error("자동 게시에 실패했습니다 (%s)." % type(e).__name__)
                if isinstance(e, RuntimeError):
                    st.write(str(e))
                st.info("아래 내용을 복사해서 Padlet에 직접 붙여 넣어도 됩니다.")
                st.code(name + "\n\n" + body, language=None)
