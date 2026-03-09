import asyncio
import streamlit as st
import os
import time
import json
from datetime import datetime
import fitz  # PyMuPDF
import google.generativeai as genai
import edge_tts
import csv
import io
import gspread
from google.oauth2.service_account import Credentials
from moviepy.editor import ImageClip, AudioFileClip, TextClip, CompositeVideoClip, concatenate_videoclips

# --- スプレッドシート連携の設定 ---
SHEET_ID = "1WdjnaH92jqiFPdlJkWeGKqRAp8tL6la8O0SMVX1jS-U"

def get_gspread_client():
    if "gcp_json" not in st.secrets:
        st.error("StreamlitのSecretsに gcp_json が設定されていません。")
        st.stop()
    
    credentials_dict = json.loads(st.secrets["gcp_json"])
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    creds = Credentials.from_service_account_info(credentials_dict, scopes=scopes)
    return gspread.authorize(creds)

# --- セッション状態の初期化と設定のデフォルト値 ---
if 'step' not in st.session_state: st.session_state.step = 1
if 'scripts' not in st.session_state: st.session_state.scripts = {}
if 'pdf_images' not in st.session_state: st.session_state.pdf_images = []

# 🌟 追加機能：アプリ起動時に1回だけ「最新のログ」から設定を自動読み込み
if 'settings_loaded' not in st.session_state:
    # まずデフォルトの初期値を入れておく
    st.session_state.target = "新入社員向け"
    st.session_state.tone = "です・ます調（丁寧）"
    st.session_state.time_sec = 20
    st.session_state.voice_type = "女性（Nanami）"
    st.session_state.speed_choice = "標準"
    st.session_state.custom_prompt = "専門用語はわかりやすく噛み砕いてください。\n明るく前向きなトーンで話してください。"
    st.session_state.dict_input = "SaaS=サアス\nMakuake=マクアケ\nKPI=ケーピーアイ"
    
    # スプレッドシートから最新1件を取得して上書きする
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SHEET_ID).sheet1
        records = sheet.get_all_values()
        if len(records) > 1:
            latest_record = records[-1] # 一番下（最新）の行を取得
            saved_settings = json.loads(latest_record[3])
            
            # 保存されている設定があれば上書き
            if "target" in saved_settings: st.session_state.target = saved_settings["target"]
            if "tone" in saved_settings: st.session_state.tone = saved_settings["tone"]
            if "time_sec" in saved_settings: st.session_state.time_sec = saved_settings["time_sec"]
            if "voice_type" in saved_settings: st.session_state.voice_type = saved_settings["voice_type"]
            if "speed_choice" in saved_settings: st.session_state.speed_choice = saved_settings["speed_choice"]
            if "custom_prompt" in saved_settings: st.session_state.custom_prompt = saved_settings["custom_prompt"]
            if "dict_input" in saved_settings: st.session_state.dict_input = saved_settings["dict_input"]
    except Exception:
        pass # 初回やエラー時はデフォルトのまま続行（画面を止めないための安全策）
        
    st.session_state.settings_loaded = True

# --- UI全体の設定 ---
st.set_page_config(page_title="PDF解説動画メーカー", page_icon="🎬", layout="wide")

# --- サイドバー (設定エリア) ---
st.sidebar.title("⚙️ 設定メニュー")
passcode = st.sidebar.text_input("🔐 パスコード", type="password")

if passcode != "20170715":
    st.sidebar.warning("正しいパスコードを入力してください。")
    st.warning("👈 左側のメニューからパスコードを入力してロックを解除してください。")
    st.stop()

api_key = st.sidebar.text_input("🔑 Gemini APIキー", type="password")

target_opts = ["新入社員向け", "既存顧客の担当者向け", "役員・決裁者向け"]
st.session_state.target = st.sidebar.selectbox("🎯 誰向けに解説しますか？", target_opts, index=target_opts.index(st.session_state.target) if st.session_state.target in target_opts else 0)

tone_opts = ["です・ます調（丁寧）", "だ・である調（少しお堅め）", "熱血営業マン風", "ニュースキャスター風"]
st.session_state.tone = st.sidebar.selectbox("🎭 トーン", tone_opts, index=tone_opts.index(st.session_state.tone) if st.session_state.tone in tone_opts else 0)

st.session_state.time_sec = st.sidebar.slider("⏳ 1ページあたりの時間", min_value=10, max_value=60, step=5, value=int(st.session_state.time_sec))

voice_opts = ["女性（Nanami）", "男性（Keita）"]
st.session_state.voice_type = st.sidebar.radio("🗣️ 音声の種類", voice_opts, index=voice_opts.index(st.session_state.voice_type) if st.session_state.voice_type in voice_opts else 0)

speed_opts = ["少しゆっくり", "標準", "少し速め", "速い"]
st.session_state.speed_choice = st.sidebar.selectbox("⏩ 話すスピード", speed_opts, index=speed_opts.index(st.session_state.speed_choice) if st.session_state.speed_choice in speed_opts else 1)

speed_map = {"少しゆっくり": "-10%", "標準": "+0%", "少し速め": "+15%", "速い": "+30%"}
use_subtitle = st.sidebar.checkbox("🔤 字幕をつける", value=True)

st.sidebar.divider()
st.sidebar.subheader("📝 AIへの追加指示 (プロンプト)")
st.session_state.custom_prompt = st.sidebar.text_area("自由に指示を追加できます", value=st.session_state.custom_prompt, height=100)

# --- メイン画面 ---
st.title("🎬 社内向け：PDF解説動画自動生成ツール")

if st.session_state.step == 1:
    st.subheader("📝 1. スライド(PDF)と台本の準備")
    
    # 🌟 辞書の入力欄も、セッション状態（自動読み込み値）をベースにする
    st.session_state.dict_input = st.text_area("📖 社内用語・読み方辞書 (例: SaaS=サアス)", value=st.session_state.dict_input)
    
    st.divider()
    
    uploaded_pdf = st.file_uploader("📄 スライド(PDF)をアップロード 【必須】", type=['pdf'])
    
    if uploaded_pdf:
        st.session_state.uploaded_pdf_name = uploaded_pdf.name
    
    script_method = st.radio("🤖 台本の作成方法を選択", ["✨ AIで自動生成 (Gemini)", "📁 CSVから読み込む", "🕒 過去の履歴から復元"])
    
    # 履歴復元のUI
    history_options = []
    history_records = []
    if script_method == "🕒 過去の履歴から復元":
        try:
            client = get_gspread_client()
            sheet = client.open_by_key(SHEET_ID).sheet1
            records = sheet.get_all_values()
            if len(records) > 1:
                history_records = records[1:] # ヘッダーを除外
                history_records.reverse() # 新しい順にする
                history_options = [f"【{row[0]}】📄 {row[1]}" for row in history_records]
                selected_history_label = st.selectbox("📂 復元するデータを選択してください", history_options)
            else:
                st.info("スプレッドシートに過去の履歴がまだありません。")
        except Exception as e:
            st.error(f"スプレッドシートの読み込みに失敗しました。設定を確認してください。エラー: {e}")

    elif script_method == "📁 CSVから読み込む":
        st.info("💡 1行目に1ページ目、2行目に2ページ目...の台本が入力されたCSVをアップロードしてください。")
        uploaded_csv = st.file_uploader("📊 台本CSVをアップロード", type=['csv'])
    else:
        uploaded_csv = None

    if st.button("🚀 次へ (台本の準備)", type="primary"):
        if not uploaded_pdf:
            st.error("PDFファイルをアップロードしてください！")
            st.stop()
            
        if script_method == "✨ AIで自動生成 (Gemini)" and not api_key:
            st.error("APIキーをセットしてください！")
            st.stop()
            
        if script_method == "📁 CSVから読み込む" and not uploaded_csv:
            st.error("CSVファイルをアップロードしてください！")
            st.stop()
            
        if script_method == "🕒 過去の履歴から復元" and not history_options:
            st.error("復元できる履歴データがありません。")
            st.stop()

        # PDFから画像を抽出
        pdf_path = "temp_uploaded.pdf"
        with open(pdf_path, "wb") as f:
            f.write(uploaded_pdf.getbuffer())

        pdf_document = fitz.open(pdf_path)
        total_pages = len(pdf_document)
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        st.session_state.pdf_images = []
        st.session_state.scripts = {}

        status_text.info("⏳ スライド画像を抽出中...")
        for page_num in range(total_pages):
            page = pdf_document[page_num]
            pix = page.get_pixmap(dpi=150)
            img_path = f"page_{page_num}.png"
            pix.save(img_path)
            st.session_state.pdf_images.append(img_path)

        # 台本の準備分岐
        if script_method == "✨ AIで自動生成 (Gemini)":
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-3.1-flash-lite-preview')
            
            for page_num in range(total_pages):
                status_text.info(f"⏳ ページ {page_num + 1}/{total_pages} の台本をAIが生成中...")
                sample_file = genai.upload_file(path=st.session_state.pdf_images[page_num])
                
                prompt = f"""あなたは{st.session_state.target}に向けて業務内容を解説する優秀なコミュニケーターです。
                このスライド画像の内容を解説する台本を作成してください。
                【条件】トーン：「{st.session_state.tone}」。約{st.session_state.time_sec}秒（{int(st.session_state.time_sec * 5)}文字）で。
                ※重要※ 記号（・〜※！？など）や箇条書きは絶対に避ける。1文を短く自然な話し言葉に。
                【追加指示】
                {st.session_state.custom_prompt}"""
                
                response = model.generate_content([sample_file, prompt])
                st.session_state.scripts[page_num] = response.text.replace('\n', ' ').strip()
                
                progress_bar.progress((page_num + 1) / total_pages)
                if page_num < total_pages - 1:
                    time.sleep(4)
                    
        elif script_method == "📁 CSVから読み込む":
            status_text.info("⏳ CSVから台本を読み込んでいます...")
            csv_text = uploaded_csv.getvalue().decode('utf-8-sig')
            reader = csv.reader(io.StringIO(csv_text))
            csv_lines = [row[0] for row in reader if row]
            
            for page_num in range(total_pages):
                if page_num < len(csv_lines):
                    st.session_state.scripts[page_num] = csv_lines[page_num]
                else:
                    st.session_state.scripts[page_num] = "（※台本データがありません。ここに入力してください）"
            progress_bar.progress(1.0)
            
        elif script_method == "🕒 過去の履歴から復元":
            status_text.info("⏳ 過去のデータを復元中...")
            selected_idx = history_options.index(selected_history_label)
            selected_row = history_records[selected_idx]
            
            saved_scripts = json.loads(selected_row[2])
            saved_settings = json.loads(selected_row[3])
            
            # 台本の復元
            for page_num in range(total_pages):
                str_page = str(page_num)
                if str_page in saved_scripts:
                    st.session_state.scripts[page_num] = saved_scripts[str_page]
                else:
                    st.session_state.scripts[page_num] = "（※当時の台本がありません。ここに入力してください）"
                    
            # 🌟 履歴復元時にも辞書を反映する
            if "target" in saved_settings: st.session_state.target = saved_settings["target"]
            if "tone" in saved_settings: st.session_state.tone = saved_settings["tone"]
            if "time_sec" in saved_settings: st.session_state.time_sec = saved_settings["time_sec"]
            if "voice_type" in saved_settings: st.session_state.voice_type = saved_settings["voice_type"]
            if "speed_choice" in saved_settings: st.session_state.speed_choice = saved_settings["speed_choice"]
            if "custom_prompt" in saved_settings: st.session_state.custom_prompt = saved_settings["custom_prompt"]
            if "dict_input" in saved_settings: st.session_state.dict_input = saved_settings["dict_input"]
            
            progress_bar.progress(1.0)

        status_text.success("✅ 下書きが準備できました！内容を確認・修正してください。")
        st.session_state.step = 2
        st.rerun()

if st.session_state.step == 2:
    st.subheader("✍️ 2. 台本の確認・修正")
    edited_scripts = {}
    for i, img_path in enumerate(st.session_state.pdf_images):
        col1, col2 = st.columns([1, 2])
        with col1:
            st.image(img_path, use_container_width=True)
        with col2:
            edited_scripts[i] = st.text_area(f"ページ {i+1} の台本", value=st.session_state.scripts[i], height=150)

    st.divider()
    if st.button("🎬 3. この台本で動画を生成する", type="primary"):
        st.session_state.scripts = edited_scripts
        dict_map = {}
        for line in st.session_state.dict_input.split('\n'):
            if '=' in line:
                k, v = line.split('=', 1)
                dict_map[k.strip()] = v.strip()

        # --------------------------------------------------
        # 💾 スプレッドシートへの履歴保存処理
        # --------------------------------------------------
        try:
            client = get_gspread_client()
            sheet = client.open_by_key(SHEET_ID).sheet1
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            pdf_name = st.session_state.get('uploaded_pdf_name', 'presentation.pdf')
            scripts_json = json.dumps(st.session_state.scripts, ensure_ascii=False)
            
            # 🌟 保存データに「辞書（dict_input）」を追加
            settings_json = json.dumps({
                "target": st.session_state.target,
                "tone": st.session_state.tone,
                "time_sec": st.session_state.time_sec,
                "voice_type": st.session_state.voice_type,
                "speed_choice": st.session_state.speed_choice,
                "custom_prompt": st.session_state.custom_prompt,
                "dict_input": st.session_state.dict_input
            }, ensure_ascii=False)
            
            sheet.append_row([timestamp, pdf_name, scripts_json, settings_json])
        except Exception as e:
            st.toast(f"⚠️ 履歴の保存に失敗しましたが、動画生成は続行します: {e}")
        # --------------------------------------------------

        status_text = st.empty()
        progress_bar = st.progress(0)
        clips = []
        total_pages = len(st.session_state.pdf_images)

        for page_num in range(total_pages):
            status_text.info(f"⏳ ページ {page_num + 1}/{total_pages} の動画を合成中...")
            img_path = st.session_state.pdf_images[page_num]
            subtitle_text = st.session_state.scripts[page_num]
            
            audio_text = subtitle_text
            for key, value in dict_map.items():
                audio_text = audio_text.replace(key, value)

            audio_path = f"audio_{page_num}.mp3"
            voice_id = "ja-JP-NanamiNeural" if "女性" in st.session_state.voice_type else "ja-JP-KeitaNeural"
            selected_rate = speed_map[st.session_state.speed_choice]
            
            import subprocess
            cmd = [
                "edge-tts",
                "--text", audio_text,
                "--voice", voice_id,
                "--rate", selected_rate,
                "--write-media", audio_path
            ]
            subprocess.run(cmd, check=True)

            audio_clip = AudioFileClip(audio_path)
            img_clip = ImageClip(img_path).set_duration(audio_clip.duration)
            
            if use_subtitle:
                try:
                    font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc" 
                    txt_clip = TextClip(subtitle_text, font=font_path, fontsize=30, color='white', bg_color='rgba(0,0,0,0.6)', size=(img_clip.w - 40, None), method='caption')
                    txt_clip = txt_clip.set_position(('center', 'bottom')).set_duration(audio_clip.duration)
                    video_clip = CompositeVideoClip([img_clip, txt_clip])
                except:
                    video_clip = img_clip
            else:
                video_clip = img_clip

            video_clip = video_clip.set_audio(audio_clip)
            clips.append(video_clip)
            progress_bar.progress((page_num + 1) / total_pages)

        status_text.info("🎬 全ページの結合をしています...")
        final_video = concatenate_videoclips(clips, method="compose")
        output_filename = "presentation_video.mp4"
        final_video.write_videofile(output_filename, fps=10, codec="libx264", audio_codec="aac", preset="ultrafast", threads=4, logger=None)

        status_text.success("✅ 完了しました！同時に履歴もスプレッドシートに保存されました。")
        st.video(output_filename)
        
        if st.button("🔄 最初からやり直す"):
            st.session_state.step = 1
            st.rerun()
