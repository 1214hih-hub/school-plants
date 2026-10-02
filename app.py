import time
import base64
import io
from urllib.parse import quote

import requests
import streamlit as st
from PIL import Image, ImageOps

st.set_page_config(page_title="우리 학교 식물 도감", page_icon="🌿")

HEADERS = {"User-Agent": "school-plants-app/1.0 (educational use)"}


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


def wiki_summary(names):
    for lang in ("ko", "en"):
        for name in names:
            if not name:
                continue
            try:
                url = "https://%s.wikipedia.org/api/rest_v1/page/summary/%s" % (lang, quote(name.replace(" ", "_")))
                r = requests.get(url, headers=HEADERS, timeout=15)
                if r.status_code != 200:
                    continue
                data = r.json()
                if data.get("type") == "disambiguation":
                    continue
                extract = (data.get("extract") or "").strip()
                if extract:
                    return extract
            except Exception:
                continue
    return ""


def identify(jpeg, key):
    r = None
    for attempt in range(3):
        try:
            r = requests.post(
                "https://my-api.plantnet.org/v2/identify/all",
                params={"api-key": key, "lang": "ko", "nb-results": 3},
                files=[("images", ("plant.jpg", jpeg, "image/jpeg"))],
                data={"organs": "auto"},
                timeout=(15, 60),
            )
             break
        except requests.exceptions.RequestException:
            time.sleep(3)
    if r is None:
        raise RuntimeError("식물 검색 서버에 연결하지 못했습니다. 잠시 후 다시 시도하세요.")
    if r.status_code == 404:
        return []
    if r.status_code != 200:
        raise RuntimeError("Pl@ntNet 오류 (%s): %s" % (r.status_code, r.text[:200]))
    out = []
    for item in r.json().get("results", [])[:3]:
        sp = item.get("species", {})
        sci = sp.get("scientificNameWithoutAuthor", "")
        commons = sp.get("commonNames", []) or []
        family = (sp.get("family") or {}).get("scientificNameWithoutAuthor", "")
        out.append(
            {
                "score": round(item.get("score", 0) * 100, 1),
                "sci": sci,
                "common": commons[0] if commons else "",
                "family": family,
                "desc": wiki_summary(commons[:2] + [sci]),
            }
        )
    return out


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
st.caption("식물 사진을 찍고 [검색하기]를 누르면 이름과 특징을 알려 줍니다.")

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
        key = secret("PLANTNET_API_KEY")
        if not key:
            st.error("Pl@ntNet API 키가 설정되지 않았습니다. 선생님께 알려 주세요.")
        else:
            try:
                jpeg = prepare_image(photo.getvalue())
                with st.spinner("식물을 찾는 중입니다..."):
                    cands = identify(jpeg, key)
                st.session_state["jpeg"] = jpeg
                st.session_state["cands"] = cands
                st.session_state["done"] = False
            except Exception as e:
                st.error("검색에 실패했습니다: %s" % e)

cands = st.session_state.get("cands")
if cands is not None:
    st.image(st.session_state["jpeg"], caption="내가 찍은 사진", use_container_width=True)
    if not cands:
        st.warning("식물을 찾지 못했습니다. 잎이나 꽃이 잘 보이게 가까이에서 다시 찍어 보세요.")
    else:
        st.subheader("후보 식물")
        st.write("실제 식물과 비교해서 가장 비슷한 것을 고르세요. 결과가 틀릴 수도 있습니다.")
        labels = []
        for c in cands:
            labels.append("%s (%s) - 일치도 %s%%" % (c["common"] or "한국어 이름 없음", c["sci"], c["score"]))
        idx = st.radio("후보 선택", range(len(cands)), format_func=lambda i: labels[i])
        c = cands[idx]

        name = st.text_input("식물 이름", value=c["common"] or c["sci"], key="name_%d" % idx)
        desc = st.text_area(
            "특징 (고쳐 써도 됩니다)",
            value=c["desc"] or "위키백과에서 설명을 찾지 못했습니다. 직접 써 보세요.",
            height=180,
            key="desc_%d" % idx,
        )
        obs = st.text_area("내가 관찰한 점 (잎 모양, 꽃 색깔, 냄새 등)", key="obs_%d" % idx)

        if st.button("📌 Padlet에 게시하기", disabled=st.session_state.get("done", False)):
            lines = [
                "학명: %s" % c["sci"],
                "과(科): %s" % (c["family"] or "알 수 없음"),
                "AI 일치도: %s%%" % c["score"],
                "",
                "[특징]",
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
                st.error("자동 게시에 실패했습니다: %s" % e)
                st.info("아래 내용을 복사해서 Padlet에 직접 붙여 넣어도 됩니다.")
                st.code(name + "\n\n" + body, language=None)
