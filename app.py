import asyncio
import streamlit as st
import os
import time
import fitz  # PyMuPDF
import google.generativeai as genai
import edge_tts
import csv
import io
from moviepy.editor import ImageClip, AudioFileClip, TextClip, CompositeVideoClip, concatenate_videoclips

# --- セッション状態の初期化 ---
if 'step' not in st.session_state:
    st.session_state.step = 1
if 'scripts' not in st.session_state:
    st.session_state.scripts = {}
if 'pdf_images' not in st.session_state:
    st.session_state.pdf_images = []

# --- UI全体の設定 ---
st.set_page_config(page_title="PDF解説動画メーカー", page_icon="🎬", layout="wide")

# --- サイドバー (設定エリア) ---
st.sidebar.title("⚙️ 設定メニュー")
passcode = st.sidebar.text_input("🔐 パスコード", type="password")

# 💡 パスワードを「20170715」に設定済みです
if passcode != "20170715":
    st.sidebar.warning("正しいパスコードを入力してください。")
    st.warning("👈 左側のメニューからパスコードを入力してロックを解除してください。")
    st.stop()

# 鍵が開いたら表示される設定
api_key = st.sidebar.text_input("🔑 Gemini APIキー", type="password")
target = st.sidebar.selectbox("🎯 誰向けに解説しますか？", ["新入社員向け", "既存顧客の担当者向け", "役員・決裁者向け"])
tone = st.sidebar.selectbox("🎭 トーン", ["です・ます調（丁寧）", "だ・である調（少しお堅め）", "熱血営業マン風", "ニュースキャスター風"])
time_sec = st.sidebar.slider("⏳ 1ページあたりの時間", min_value=10, max_value=60, value=20, step=5)
voice_type = st.sidebar.radio("🗣️ 音声の種類", ["女性（Nanami）", "男性（Keita）"])
speed_choice = st.sidebar.selectbox("⏩ 話すスピード", ["少しゆっくり", "標準", "少し速め", "速い"], index=1)
speed_map = {"少しゆっくり": "-10%", "標準": "+0%", "少し速め": "+15%", "速い": "+30%"}
use_subtitle = st.sidebar.checkbox("🔤 字幕をつける", value=True)

# 🆕 追加機能①：プロンプト調整欄
st.sidebar.divider()
st.sidebar.subheader("📝 AIへの追加指示 (プロンプト)")
custom_prompt = st.sidebar.text_area(
    "自由に指示を追加できます", 
    value="専門用語はわかりやすく噛み砕いてください。\n明るく前向きなトーンで話してください。",
    height=100
)

# --- メイン画面 ---
st.title("🎬 社内向け：PDF解説動画自動生成ツール")

if st.session_state.step == 1:
    st.subheader("📝 1. スライド(PDF)と台本の準備")
    
    dict_input = st.text_area("📖 社内用語・読み方辞書 (例: SaaS=サアス)", value="SaaS=サアス\nMakuake=マクアケ\nKPI=ケーピーアイ")
    st.session_state.dict_input = dict_input
    
    st.divider()
    
    # 常にPDFはアップロードする（画像抽出のため）
    uploaded_pdf = st.file_uploader("📄 スライド(PDF)をアップロード 【必須】", type=['pdf'])
    
    # 🆕 追加機能②：台本の作成方法の選択
    script_method = st.radio("🤖 台本の作成方法を選択", ["✨ AIで自動生成 (Gemini)", "📁 CSVから読み込む (手動作成済みの場合)"])
    
    if script_method == "📁 CSVから読み込む (手動作成済みの場合)":
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
            
        if script_method == "📁 CSVから読み込む (手動作成済みの場合)" and not uploaded_csv:
            st.error("CSVファイルをアップロードしてください！")
            st.stop()

        # 共通処理：PDFから画像を抽出
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
                
                # プロンプトにサイドバーの custom_prompt を合流させる
                prompt = f"""あなたは{target}に向けて業務内容を解説する優秀なコミュニケーターです。
                このスライド画像の内容を解説する台本を作成してください。
                【条件】トーン：「{tone}」。約{time_sec}秒（{int(time_sec * 5)}文字）で。
                ※重要※ 記号（・〜※！？など）や箇条書きは絶対に避ける。1文を短く自然な話し言葉に。
                【追加指示】
                {custom_prompt}"""
                
                response = model.generate_content([sample_file, prompt])
                st.session_state.scripts[page_num] = response.text.replace('\n', ' ').strip()
                
                progress_bar.progress((page_num + 1) / total_pages)
                if page_num < total_pages - 1:
                    time.sleep(4)
        else:
            # CSVから読み込む処理
            status_text.info("⏳ CSVから台本を読み込んでいます...")
            csv_text = uploaded_csv.getvalue().decode('utf-8-sig')  # エクセル由来の文字化けを防ぐおまじない
            reader = csv.reader(io.StringIO(csv_text))
            
            # 空行を無視して最初の列（A列）の文字を取得
            csv_lines = [row[0] for row in reader if row]
            
            for page_num in range(total_pages):
                if page_num < len(csv_lines):
                    st.session_state.scripts[page_num] = csv_lines[page_num]
                else:
                    st.session_state.scripts[page_num] = "（※台本データがありません。ここに入力してください）"
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
            voice_id = "ja-JP-NanamiNeural" if "女性" in voice_type else "ja-JP-KeitaNeural"
            selected_rate = speed_map[speed_choice]
            
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

        status_text.success("✅ 完成しました！")
        st.video(output_filename)
        
        if st.button("🔄 最初からやり直す"):
            st.session_state.step = 1
            st.rerun()
