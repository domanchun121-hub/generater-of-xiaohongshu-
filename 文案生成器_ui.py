import os
import json
import hashlib
import time
import logging
import urllib.parse
import streamlit as st
import streamlit.components.v1 as components
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI, RateLimitError, AuthenticationError

# ==========================================
# 页面配置
# ==========================================
st.set_page_config(
    page_title="🔥 爆款文案生成器",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 自定义 CSS，让界面更好看
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    /* 主标题 */
    .main-title {
        background: linear-gradient(135deg, #ff6b6b, #feca57, #ff9ff3);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 2.5rem;
        font-weight: 700;
        text-align: center;
        padding: 1rem 0;
    }

    /* 结果卡片 */
    .result-card {
        background: linear-gradient(135deg, #1a1a2e, #16213e);
        border: 1px solid #ff6b6b40;
        border-radius: 16px;
        padding: 1.5rem;
        margin-top: 1rem;
    }

    /* 状态徽章 */
    .badge-cached {
        background: #00b894;
        color: white;
        padding: 2px 10px;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .badge-fresh {
        background: #e17055;
        color: white;
        padding: 2px 10px;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 600;
    }

    /* 按钮美化 */
    .stButton > button {
        width: 100%;
        background: linear-gradient(135deg, #ff6b6b, #ee5a24);
        color: white;
        border: none;
        border-radius: 12px;
        padding: 0.75rem;
        font-size: 1.1rem;
        font-weight: 600;
        transition: all 0.3s ease;
        box-shadow: 0 4px 15px rgba(255, 107, 107, 0.4);
    }
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(255, 107, 107, 0.6);
    }

    /* textarea 美化 */
    .stTextArea textarea {
        border-radius: 10px;
        border: 1px solid #ff6b6b40;
        background: #1a1a2e;
        color: #eee;
    }

    /* sidebar 背景 */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f0c29, #302b63);
    }
    [data-testid="stSidebar"] * {
        color: #eee !important;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 全局配置
# ==========================================
base_dir = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(base_dir, "api_cache.json")
HISTORY_FILE = os.path.join(base_dir, "history.json")
MAX_EXAMPLE_POSTS = 5

# ==========================================
# 缓存模块
# ==========================================
def load_cache() -> dict:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}

def save_cache(cache_data: dict):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=4)

def get_hash(text: str) -> str:
    return hashlib.md5(text.encode('utf-8')).hexdigest()

# ==========================================
# 历史记录模块
# ==========================================
def load_history() -> list:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []

def save_history(history_data: list):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history_data, f, ensure_ascii=False, indent=4)

def add_to_history(topic: str, text: str):
    history_data = load_history()
    item = {
        "id": datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "topic": topic,
        "text": text
    }
    history_data.insert(0, item) # 最新记录插到最前
    history_data = history_data[:50] # 仅保留最近的50条
    save_history(history_data)

# ==========================================
# Prompt 构建
# ==========================================
def analyze_and_generate_prompt(viral_posts: list, target_topic: str, max_tokens_output: int):
    posts = viral_posts[:MAX_EXAMPLE_POSTS]
    texts = [p["text"] if isinstance(p, dict) else p for p in posts]
    avg_length = sum(len(t) for t in texts) // len(texts) if texts else 300
    examples_text = "\n\n".join(f"【案例 {i+1}】:\n{t}" for i, t in enumerate(texts))

    system_instruction = "你是一个顶级的爆款内容创作者和 NLP 文本分析专家。你擅长从爆款案例中提炼风格 DNA，然后用这套风格创作出情节全新、细节丰富、独立成篇的内容。你的创作原则：风格高度还原，情节绝对原创。"
    user_instruction_template = f"""请仔细阅读以下爆款案例，深度分析它们的风格特征：

{{examples_text}}

---

【你的任务】基于上述案例的**风格 DNA**，为我创作一篇关于「{{target_topic}}」的全新帖子。

**字数要求**：{{avg_length}} 字左右（±20%）

**风格要求（必须严格遵守）**：
- 复刻语气：情绪浓度、口语化程度、感叹/疑问句比例
- 复刻结构：开头钩子、中间展开方式、结尾行动引导
- 复刻排版：短句断行、分段节奏、Emoji 使用密度和位置
- 复刻引导词：类似的转折词、递进词、呼吁性词汇

**内容要求（同样必须严格遵守）**：
- ❌ 禁止复制或改写原案例中的任何具体情节、场景、产品、人物
- ✅ 必须构建与原案例**完全不同**的具体故事场景
- ✅ 细节要丰富：有具体时间、地点、感受、对比、转折，不能泛泛而谈
- ✅ 情绪要真实：有真实的痛点铺垫，有真实的惊喜/收获，不能只讲结论
- ✅ 每次生成的内容必须是独特的，即使主题相同

**输出格式**：
1. **直接输出正文，禁止输出“风格特征摘要”等前置分析内容**
2. **正文必须使用 Markdown 格式**，并且：
   - 使用 `@---` 来强制分页（每页内容不要太多）
   - 适当使用 `**加粗**` 突出核心词元或金句
   - 合理使用一级标题 `#` 和二级标题 `##` 划分结构
"""
    
    # Try reading from external template file
    try:
        template_path = os.path.join(base_dir, "prompt_template.md")
        with open(template_path, "r", encoding="utf-8") as f:
            template_content = f.read()
            # Basic parsing of the markdown sections
            sys_parts = template_content.split("## 系统提示词 (System Prompt)")
            if len(sys_parts) > 1:
                user_parts = sys_parts[1].split("## 用户提示词 (User Prompt)")
                if len(user_parts) > 1:
                    system_instruction = user_parts[0].strip()
                    user_instruction_template = user_parts[1].strip()
    except Exception as e:
        st.warning("未找到 prompt_template.md 或是解析失败，使用内置默认 Prompt。")

    user_instruction = user_instruction_template.format(
        examples_text=examples_text,
        target_topic=target_topic,
        avg_length=avg_length
    )
    return system_instruction, user_instruction

# ==========================================
# API 调用（含重试 + 缓存 + 成本控制）
# ==========================================
def generate_content(system_prompt: str, user_prompt: str, api_key: str,
                     model: str, max_tokens: int, temperature: float = 0.9,
                     retries: int = 3, variant_id: int = 0):
    """返回 (text, is_from_cache, error_msg)。variant_id 用于区分同一 prompt 的多次并发调用的缓存 key"""
    # variant_id 保证每个并发变体有独立的缓存 key，不会互相命中
    prompt_hash = get_hash(system_prompt + user_prompt + model + str(variant_id))
    cache = load_cache()

    if prompt_hash in cache:
        return cache[prompt_hash], True, None

    if not api_key:
        return None, False, "请先在左侧侧边栏填入 DeepSeek API Key！"

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )

    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            text = response.choices[0].message.content
            cache[prompt_hash] = text
            save_cache(cache)
            return text, False, None

        except AuthenticationError:
            return None, False, "❌ API Key 无效，请检查后重试。"
        except RateLimitError:
            wait = 2 ** attempt * 5
            st.warning(f"触发限速，{wait} 秒后重试... ({attempt+1}/{retries})")
            time.sleep(wait)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(3)
            else:
                return None, False, f"❌ API 调用失败：{e}"

    return None, False, "已达到最大重试次数，请稍后再试。"

def format_content(text: str, api_key: str, max_tokens: int, retries: int = 3, variant_id: int = 0):
    system_prompt = "你是一个专业的小红书爆款排版专家。你的唯一任务是严格依据指令为提供的文案增加 Emoji 表情和换行符，【绝对禁止】改写或删减原有的任何文字内容。"
    user_prompt = f"""请为以下文案进行排版加工作业（fast 模式排版），必须严格遵守以下 3 条指令：

1. 【自然插入表情】：每个由 `@---` 分隔的画布中，必须包含 3 到 5 个符合语境的 Emoji。**🚫绝对禁止**像列表一样机械地在每一行末尾都加表情！表情应该自然地跟在核心词汇后面（如：科技感✨），或者穿插在句首/句中，做到错落有致、有呼吸感。
2. 【软换行与留白】：
   - 在**每一行文字的末尾**（除了完全空白的行和只有 `@---` 的行），强制添加**两个空格**再回车，触发软换行。
   - 保留原句之间的空行（空行可以营造呼吸感）。如果连续几行文字太密集，允许你在大逻辑转折的地方插入一个空行。
3. 【保持原意】：绝对禁止对原文进行删减、概括或改写！请原封不动地返回原文的所有词句。不要输出任何解释性文字。

【需要排版的原始文案如下】：
{text}
"""
    # 强制使用 deepseek-chat 进行格式化（速度快），降低温度确保稳定输出
    # 强制限制为 8192，因为 deepseek-chat api 要求的最大 tokens 是 8192
    format_max_tokens = min(max_tokens, 8192)
    return generate_content(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        api_key=api_key,
        model="deepseek-chat",
        max_tokens=format_max_tokens,
        temperature=0.1,
        retries=retries,
        variant_id=variant_id + 1000  # 偏移variant_id，防止和第一步的缓存互相碰撞
    )

# ==========================================
# 侧边栏配置
# ==========================================
with st.sidebar:
    st.markdown("## ⚙️ 配置")
    st.markdown("---")

    # API Key（优先读环境变量）
    env_key = os.environ.get("DEEPSEEK_API_KEY", "")
    api_key_input = st.text_input(
        "🔑 DeepSeek API Key",
        value=env_key,
        type="password",
        help="也可以设置环境变量 DEEPSEEK_API_KEY，自动填入",
        placeholder="sk-..."
    )

    st.markdown("---")
    st.markdown("### 🎛️ 生成参数")

    model_choice = st.selectbox(
        "模型选择",
        ["deepseek-chat", "deepseek-reasoner"],
        index=0,
        help="deepseek-chat 速度快价格低，deepseek-reasoner 推理能力更强"
    )

    max_tokens_slider = st.slider(
        "最大输出 Token（成本控制）",
        min_value=500,
        max_value=32000,
        value=2000,
        step=500,
        help="Token ≈ 字数 × 1.5｜上万字需要 15000+ Token｜deepseek-chat 上限约 8192，deepseek-reasoner 上限 32768"
    )

    temperature_slider = st.slider(
        "创意度 Temperature",
        min_value=0.5,
        max_value=1.5,
        value=0.95,
        step=0.05,
        help="越高越有创意但越不稳定；0.9~1.1 适合爆款文案"
    )

    retries_input = st.number_input("最大重试次数", min_value=1, max_value=5, value=3)

    st.markdown("---")
    st.markdown("### ⚡ 并发生成")
    num_variants = st.slider(
        "同时生成变体数",
        min_value=1,
        max_value=5,
        value=1,
        step=1,
        help="同时发起 N 个 API 请求，生成风格相同但情节不同的 N 篇文案"
    )

    st.markdown("---")
    st.markdown("### 📦 缓存状态")
    cache_data = load_cache()
    st.metric("已缓存条数", len(cache_data))
    if st.button("🗑️ 清除缓存", help="删除所有缓存记录"):
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
        st.success("缓存已清除！")
        st.rerun()

    st.markdown("---")
    st.markdown("### 📂 生成历史记录")
    history_data = load_history()
    if history_data:
        with st.expander(f"查看近期 {len(history_data)} 条记录", expanded=False):
            for i, item in enumerate(history_data):
                st.markdown(f"**{item['time']}**")
                st.caption(f"主题: {item['topic'][:15]}...")
                if st.button("恢复到画布", key=f"hist_{item['id']}", use_container_width=True):
                    st.session_state.editor_content = item['text']
                    st.session_state.editor_title = item['topic']
                    st.session_state.show_editor = True
                    st.rerun()
                st.divider()
        if st.button("🗑️ 清除历史记录", key="clear_hist", use_container_width=True):
            if os.path.exists(HISTORY_FILE):
                os.remove(HISTORY_FILE)
            st.success("历史记录已清除！")
            st.rerun()
    else:
        st.info("暂无历史记录，开始生成后将自动保存近期文案。")

# ==========================================
# 主界面
# ==========================================
st.markdown('<div class="main-title">🔥 爆款文案生成器</div>', unsafe_allow_html=True)
st.markdown(
    "<p style='text-align:center;color:#aaa;'>输入爆款案例 → AI 提取风格 → 一键生成同款文案</p>",
    unsafe_allow_html=True,
)
st.markdown("---")

col_left, col_right = st.columns([1, 1], gap="large")

with col_left:
    st.markdown("### 📥 输入区")

    # 输入方式选择
    input_mode = st.radio(
        "输入方式",
        ["✏️ 手动输入帖子", "📂 上传 JSON 文件"],
        horizontal=True,
        label_visibility="collapsed"
    )

    viral_posts = []

    if input_mode == "✏️ 手动输入帖子":
        st.markdown("**爆款帖子案例**（用**空行**隔开不同帖子，单条帖子内可以正常换行）")
        raw_posts_text = st.text_area(
            "帖子内容",
            height=250,
            placeholder="帖子一（可以多行）\n第二行继续帖子一\n\n← 空行分隔 →\n\n帖子二从这里开始\n继续帖子二的内容",
            label_visibility="collapsed"
        )
        if raw_posts_text.strip():
            # 用空行（连续两个\n）分割，保留帖子内部的换行
            blocks = [b.strip() for b in raw_posts_text.split("\n\n") if b.strip()]
            viral_posts = blocks

    else:
        uploaded = st.file_uploader("上传 JSON 文件", type=["json"])
        if uploaded:
            try:
                data = json.load(uploaded)
                # 支持 {"posts": [...]} 或直接 [...] 两种格式
                if isinstance(data, list):
                    viral_posts = data
                elif isinstance(data, dict) and "posts" in data:
                    viral_posts = data["posts"]
                else:
                    st.error("JSON 格式不支持，需要 `{\"posts\": [...]}` 或 `[...]`")
            except Exception as e:
                st.error(f"JSON 解析失败: {e}")

    if viral_posts:
        st.success(f"✅ 已加载 {len(viral_posts)} 条帖子（最多使用前 {MAX_EXAMPLE_POSTS} 条）")

    st.markdown("**目标主题**")
    topic_input = st.text_input(
        "目标主题",
        placeholder="例如：推荐一款适合新手的理财记账 App",
        label_visibility="collapsed"
    )

    generate_btn = st.button("🚀 开始生成", use_container_width=True)

with col_right:
    st.markdown("### 📤 生成结果")

    # 用 session_state 保留上次结果
    if "last_result" not in st.session_state:
        st.session_state.last_result = None
        st.session_state.last_is_cached = False

    if "results" not in st.session_state:
        st.session_state.results = []  # list of (text, is_cached)

    if generate_btn:
        if not viral_posts:
            st.error("请先输入至少 1 条爆款帖子！")
        elif not topic_input.strip():
            st.error("请填写目标主题！")
        else:
            sys_p, usr_p = analyze_and_generate_prompt(viral_posts, topic_input, max_tokens_slider)
            n = int(num_variants)

            placeholder = st.empty()
            with placeholder:
                st.info(f"🚀 正在并发生成 {n} 篇文案，请稍候...")

            def _call(vid):
                # 第一步：原样生成文案初稿
                base_text, is_cached1, err1 = generate_content(
                    system_prompt=sys_p,
                    user_prompt=usr_p,
                    api_key=api_key_input,
                    model=model_choice,
                    max_tokens=max_tokens_slider,
                    temperature=temperature_slider,
                    retries=int(retries_input),
                    variant_id=vid,
                )
                if err1:
                    return vid, (None, False, err1)
                
                # 第二步：使用 fast 模式补充排版（表情+软换行）
                final_text, is_cached2, err2 = format_content(
                    text=base_text,
                    api_key=api_key_input,
                    max_tokens=max_tokens_slider,
                    retries=int(retries_input),
                    variant_id=vid,
                )
                if err2:
                    return vid, (None, False, f"第一步生成成功，但第二步排版时发生错误：{err2}")
                
                # 综合缓存状态
                is_cached = is_cached1 and is_cached2
                return vid, (final_text, is_cached, None)

            results_raw = [None] * n
            with ThreadPoolExecutor(max_workers=n) as executor:
                futures = {executor.submit(_call, i): i for i in range(n)}
                for future in as_completed(futures):
                    vid, (text, is_cached, err) = future.result()
                    if err:
                        st.error(f"变体 {vid+1} 失败：{err}")
                        results_raw[vid] = None
                    else:
                        results_raw[vid] = (text, is_cached)

            placeholder.empty()
            st.session_state.results = [r for r in results_raw if r is not None]
            
            # 将新生成的保存至历史记录
            for text, is_cached in st.session_state.results:
                if not is_cached:
                    add_to_history(topic_input, text)

    if st.session_state.results:
        results = st.session_state.results
        tab_labels = [f"📄 变体 {i+1}{'  ⚡缓存' if r[1] else ''}" for i, r in enumerate(results)]
        tabs = st.tabs(tab_labels)

        for i, (tab, (text, is_cached)) in enumerate(zip(tabs, results)):
            with tab:
                with st.expander("🔍 预览（渲染效果）", expanded=True):
                    st.markdown(text)
                with st.expander("📄 原始 Markdown"):
                    st.code(text, language="markdown")
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                
                col_btn1, col_btn2 = st.columns(2)
                with col_btn1:
                    if st.button("🎨 到画布编辑并成图", key=f"edit_{i}", use_container_width=True):
                        st.session_state.editor_content = text
                        st.session_state.editor_title = topic_input if topic_input else "生成文案"
                        st.session_state.show_editor = True
                        st.rerun()
                with col_btn2:
                    st.download_button(
                        label="⬇️ 下载此变体",
                        data=text.encode("utf-8"),
                        file_name=f"viral_post_v{i+1}_{timestamp}.md",
                        mime="text/markdown",
                        use_container_width=True,
                        key=f"dl_{i}_{timestamp}",
                    )
    else:
        st.info("👈 左侧填写帖子和主题后，点击「开始生成」")

# Show editor at the bottom if requested
if st.session_state.get("show_editor", False):
    st.markdown("---")
    col_title, col_close = st.columns([0.9, 0.1])
    with col_title:
        st.markdown("### 🎨 爆款图文编辑器 工作台")
    with col_close:
        if st.button("❌ 关闭", use_container_width=True):
            st.session_state.show_editor = False
            st.rerun()
            
    try:
        editor_path = os.path.join(base_dir, "文案到图片生成.py")
        with open(editor_path, "r", encoding="utf-8") as f:
            html_template = f.read()
            
        content_encoded = urllib.parse.quote(st.session_state.editor_content)
        title_encoded = urllib.parse.quote(st.session_state.editor_title)
        
        inject_script = f"""
        <script>
        window.addEventListener('DOMContentLoaded', () => {{
            setTimeout(() => {{
                let titleEl = document.getElementById('input-title');
                let contentEl = document.getElementById('input-content');
                if(titleEl) titleEl.value = decodeURIComponent('{title_encoded}');
                if(contentEl) contentEl.value = decodeURIComponent('{content_encoded}');
                if (typeof updatePreview === 'function') updatePreview();
            }}, 100);
        }});
        </script>
        </head>
        """
        html_code = html_template.replace("</head>", inject_script)
        
        components.html(html_code, height=900, scrolling=True)
        
    except Exception as e:
        st.error(f"加载编辑器失败: {e}")
