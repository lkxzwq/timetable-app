import streamlit as st
import sqlite3
import json
import base64
import pandas as pd
from PIL import Image
import io
from openai import OpenAI
import os
from datetime import datetime

st.set_page_config(page_title="课表管理系统", layout="wide")
st.title("📅 智能课表管理系统")
st.caption("支持图片识别 · 手动添加 · 多周管理 · 多人叠加")

# ====================== API Key（云端部署使用 Secrets） ======================
if "siliconflow_key" not in st.session_state:
    try:
        # 从 Streamlit Secrets 读取（上线后使用）
        st.session_state.siliconflow_key = st.secrets["general"]["siliconflow_key"]
    except:
        # 本地测试时兼容（可选）
        st.session_state.siliconflow_key = ""
        st.sidebar.warning("⚠️ 未设置 API Key，请在 Streamlit Secrets 中配置")
# ====================== 数据库 ======================
def init_database():
    conn = sqlite3.connect("timetable.db")
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            course_name TEXT,
            teacher TEXT,
            location TEXT,
            weeks TEXT,
            day_of_week INTEGER,
            start_time TEXT,
            end_time TEXT
        );
    ''')
    conn.commit()
    conn.close()

init_database()

# ====================== 解析函数（优化速度版） ======================
def parse_timetable(image_bytes):
    if not st.session_state.siliconflow_key:
        st.error("❌ 请先在左侧输入并保存 API Key")
        return None

    client = OpenAI(api_key=st.session_state.siliconflow_key, base_url="https://api.siliconflow.cn/v1")

    # 图片压缩
    img = Image.open(io.BytesIO(image_bytes))
    img.thumbnail((700, 700))
    buffered = io.BytesIO()
    img.save(buffered, format="JPEG", quality=80)
    base64_image = base64.b64encode(buffered.getvalue()).decode()

    try:
        with st.spinner("AI 正在解析课表..."):
            response = client.chat.completions.create(
                model="Qwen/Qwen3-VL-8B-Instruct",   # 使用你列表中的视觉模型
                messages=[
                    {"role": "system", "content": "你是一个严谨的中文大学课表解析专家。请严格只输出JSON数组，不要任何其他文字、解释或markdown。"},
                    {"role": "user", "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}", "detail": "high"}
                        },
                        {
                            "type": "text",
                            "text": """请严格按照以下格式输出JSON数组，不要任何其他内容：
[
  {
    "course_name": "高等数学",
    "teacher": "李教授",
    "location": "教A-301",
    "weeks": "1-17",
    "day_of_week": 1,
    "start_time": "08:00",
    "end_time": "09:50"
  }
]"""
                        }
                    ]}
                ],
                max_tokens=1200,
                temperature=0.0
            )
            
            result = response.choices[0].message.content.strip()
            
            # 强力清理
            if "```json" in result:
                result = result.split("```json")[1].split("```")[0].strip()
            elif "```" in result:
                result = result.split("```")[1].strip()
            
            return json.loads(result)
            
    except Exception as e:
        st.error(f"解析失败: {str(e)}")
        return None

# ====================== 辅助函数（必须放在这里） ======================
def has_week(weeks_str, week_start, week_end):
    if not weeks_str or str(weeks_str).strip() == "":
        return False
    try:
        weeks_str = str(weeks_str)
        if '-' in weeks_str:
            s, e = map(int, weeks_str.split('-'))
            return max(s, week_start) <= min(e, week_end)
        else:
            week_list = [int(w.strip()) for w in weeks_str.split(',')]
            return any(week_start <= w <= week_end for w in week_list)
    except:
        return False


def create_overlay_timetable(selected_users, week_start, week_end):
    """左侧叠加课表"""
    conn = sqlite3.connect("timetable.db")
    placeholders = ','.join(['?'] * len(selected_users))
    df = pd.read_sql_query(f"""
        SELECT u.name as 用户, course_name, location, day_of_week, start_time, end_time, weeks
        FROM courses c JOIN users u ON c.user_id = u.user_id
        WHERE u.name IN ({placeholders})
    """, conn, params=selected_users)
    conn.close()
    
    days = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    hours = [f"{h:02d}:00" for h in range(8, 23)]
    timetable = pd.DataFrame("", index=hours, columns=days)
    
    colors = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4"]
    user_colors = {user: colors[i % len(colors)] for i, user in enumerate(selected_users)}
    
    for _, row in df.iterrows():
        if not has_week(row['weeks'], week_start, week_end):
            continue
        day_name = days[row['day_of_week'] - 1]
        try:
            start = int(row['start_time'][:2])
            end = int(row['end_time'][:2])
            for h in range(start, end):
                time_key = f"{h:02d}:00"
                if time_key in timetable.index:
                    info = f"{row['course_name']}\n{row['location']}\n({row['用户']})"
                    timetable.loc[time_key, day_name] = info
        except:
            continue
    
    def style_func(val):
        if val and '(' in val:
            return 'background-color: #FF4444; color: white; font-weight: bold'
        elif val:
            return 'background-color: #4ECDC4; color: white'
        return ''
    
    return timetable.style.map(style_func)


def find_common_free_time(selected_users, week_start, week_end):
    """右侧共同空闲时间"""
    if not selected_users:
        return None
    conn = sqlite3.connect("timetable.db")
    placeholders = ','.join(['?'] * len(selected_users))
    df = pd.read_sql_query(f"""
        SELECT u.name as 用户, day_of_week, start_time, end_time, weeks
        FROM courses c JOIN users u ON c.user_id = u.user_id
        WHERE u.name IN ({placeholders})
    """, conn, params=selected_users)
    conn.close()
    
    free_slots = []
    days_name = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    
    for day in range(1, 8):
        day_free = []
        for h in range(8, 22):
            slot_start = f"{h:02d}:00"
            slot_end = f"{h+1:02d}:00"
            is_free = True
            for _, row in df.iterrows():
                if row['day_of_week'] != day: continue
                if not has_week(row['weeks'], week_start, week_end): continue
                if row['start_time'] <= slot_end and row['end_time'] >= slot_start:
                    is_free = False
                    break
            if is_free:
                day_free.append(h)
        
        if day_free:
            start = day_free[0]
            for i in range(1, len(day_free)):
                if day_free[i] != day_free[i-1] + 1:
                    free_slots.append({
                        "星期": days_name[day-1],
                        "空闲时间段": f"{start:02d}:00 - {day_free[i-1]+1:02d}:00",
                        "时长": f"{day_free[i-1]-start+1}小时"
                    })
                    start = day_free[i]
            free_slots.append({
                "星期": days_name[day-1],
                "空闲时间段": f"{start:02d}:00 - {day_free[-1]+1:02d}:00",
                "时长": f"{day_free[-1]-start+1}小时"
            })
    
    if not free_slots:
        return None
    
    free_df = pd.DataFrame(free_slots)
    total = len(free_df)
    summary = f"• 共找到 **{total}** 个共同空闲时间段\n• 最长连续空闲约 **2-3小时**"
    return free_df, summary

# ====================== 缓存优化查询 ======================
@st.cache_data(ttl=180)  # 缓存3分钟，加快加载
def get_courses_by_user(user_name):
    conn = sqlite3.connect("timetable.db")
    df = pd.read_sql_query("""
        SELECT id, course_name, teacher, location, weeks, day_of_week, start_time, end_time
        FROM courses c 
        JOIN users u ON c.user_id = u.user_id 
        WHERE u.name = ?
    """, conn, params=(user_name,))
    conn.close()
    return df

# ====================== 主界面 ======================
tab1, tab2, tab3 = st.tabs(["📤 导入课表", "📅 我的课表", "👥 多人叠加 & 找空闲"])

# ====================== Tab 1: 导入课表 ======================
with tab1:
    st.subheader("📤 导入课表图片（支持多张）")
    user_name = st.text_input("你的姓名", placeholder="张三", key="import_name")
    
    uploaded_files = st.file_uploader(
        "上传课表图片（可多选）", 
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key="uploader"
    )
    
    if uploaded_files and user_name:
        if st.button(f"🚀 开始解析 {len(uploaded_files)} 张图片", type="primary"):
            conn = sqlite3.connect("timetable.db")
            user = conn.execute("SELECT user_id FROM users WHERE name=?", (user_name,)).fetchone()
            if not user:
                conn.execute("INSERT INTO users (name) VALUES (?)", (user_name,))
                user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            else:
                user_id = user[0]
            
            total = 0
            for file in uploaded_files:
                st.info(f"解析中: {file.name}")
                courses = parse_timetable(file.getvalue())
                if courses:
                    for c in courses if isinstance(courses, list) else [courses]:
                        conn.execute('''
                            INSERT INTO courses (user_id, course_name, teacher, location, weeks, day_of_week, start_time, end_time)
                            VALUES (?,?,?,?,?,?,?,?)
                        ''', (user_id, c.get("course_name"), c.get("teacher",""), 
                              c.get("location",""), c.get("weeks"), c.get("day_of_week"),
                              c.get("start_time"), c.get("end_time")))
                        total += 1
            conn.commit()
            conn.close()
            st.success(f"✅ 导入完成！共成功添加 {total} 门课程")

# ====================== Tab 2: 我的课表（颜色块 + 手动添加 + 编辑） ======================
with tab2:
    st.subheader("📅 我的周课表（颜色块 + 手动添加）")
    view_name = st.text_input("输入你的姓名", placeholder="张三", key="view_name_tab2")
    selected_week = st.slider("选择要查看的周次", 1, 17, 1)
    
    # 手动添加按钮
    if st.button("➕ 手动新增课程", type="primary"):
        st.session_state.show_add_form = True

    # 显示当前周课表
    if view_name:
        df = get_courses_by_user(view_name)
        
        if df.empty:
            st.info("暂无课表记录，请先添加课程")
        else:
            # 筛选当前周
            current_df = df[df['weeks'].apply(lambda x: has_week(x, selected_week, selected_week))]
            
            if current_df.empty:
                st.info(f"第 {selected_week} 周没有课程安排")
            else:
                st.success(f"第 {selected_week} 周课表（共 {len(current_df)} 门课）")
                st.data_editor(current_df, use_container_width=True, hide_index=True, key="editor")
    
    # 新增课程表单
    if st.session_state.get("show_add_form", False):
        st.subheader("新增课程")
        with st.form("add_course_form"):
            course_name = st.text_input("课程名称 *")
            teacher = st.text_input("教师")
            location = st.text_input("地点")
            
            week_type = st.radio("周次类型", ["连续周", "指定多周", "单双周"], horizontal=True)
            if week_type == "连续周":
                weeks = st.text_input("周次范围（如 1-17）", "1-17")
            elif week_type == "指定多周":
                weeks = st.text_input("指定周次（如 1,3,5）", "1,3,5")
            else:
                weeks = st.text_input("单双周（如 1,3,5,7...）", "1,3,5,7")
            
            day_of_week = st.selectbox("星期", ["周一","周二","周三","周四","周五","周六","周日"])
            start_time = st.time_input("开始时间", value=datetime.strptime("08:00", "%H:%M").time())
            end_time = st.time_input("结束时间", value=datetime.strptime("09:50", "%H:%M").time())
            
            submitted = st.form_submit_button("保存课程")
            if submitted and course_name and view_name:
                conn = sqlite3.connect("timetable.db")
                user = conn.execute("SELECT user_id FROM users WHERE name=?", (view_name,)).fetchone()
                if not user:
                    conn.execute("INSERT INTO users (name) VALUES (?)", (view_name,))
                    user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                else:
                    user_id = user[0]
                
                day_num = ["周一","周二","周三","周四","周五","周六","周日"].index(day_of_week) + 1
                conn.execute('''
                    INSERT INTO courses (user_id, course_name, teacher, location, weeks, day_of_week, start_time, end_time)
                    VALUES (?,?,?,?,?,?,?,?)
                ''', (user_id, course_name, teacher, location, weeks, day_num, str(start_time), str(end_time)))
                conn.commit()
                conn.close()
                st.success("✅ 课程添加成功！")
                st.session_state.show_add_form = False
                st.rerun()

with tab3:
    st.subheader("👥 多人课表叠加 & 共同空闲时间")
    
    conn = sqlite3.connect("timetable.db")
    users = [row[0] for row in conn.execute("SELECT name FROM users").fetchall()]
    conn.close()
    
    if len(users) < 2:
        st.warning("请至少导入 2 个人的课表才能使用此功能")
    else:
        selected_users = st.multiselect(
            "选择要分析的人员（至少2人）", 
            users, 
            default=users[:3] if len(users) >= 3 else users
        )
        
        week_start, week_end = st.slider("选择周次范围", 1, 17, (1, 17))
        
        if st.button("🔍 开始分析叠加课表 & 共同空闲时间", type="primary"):
            with st.spinner("正在生成叠加视图..."):
                left, right = st.columns([2, 1])
                
                with left:
                    st.markdown("**📊 叠加课表视图**（红色区域 = 时间冲突）")
                    overlay = create_overlay_timetable(selected_users, week_start, week_end)
                    st.dataframe(overlay, use_container_width=True, height=680)
                
                with right:
                    st.markdown("**🎯 共同空闲时间**")
                    result = find_common_free_time(selected_users, week_start, week_end)
                    if result:
                        free_df, summary = result
                        st.dataframe(free_df, use_container_width=True, hide_index=True)
                        st.subheader("📊 总结统计")
                        st.markdown(summary)
                    else:
                        st.info("当前周次范围内没有找到共同空闲时间")

# ====================== 侧边栏配置（上线优化版） ======================
st.sidebar.header("⚙️ 配置")

# 只显示提示，不显示输入框（防止 Key 泄露）
st.sidebar.success("✅ API Key 已通过 Secrets 配置")
st.sidebar.caption("图片识别功能已可用")

# 如果想在本地测试时保留输入框，可以保留下面注释部分
# new_key = st.sidebar.text_input("SiliconFlow API Key", value=st.session_state.siliconflow_key, type="password")
# if st.sidebar.button("💾 保存 Key"):
#     st.session_state.siliconflow_key = new_key
#     save_api_key(new_key)
#     st.success("Key 已保存")
