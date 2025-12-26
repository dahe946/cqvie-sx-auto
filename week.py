import requests
import json
import datetime
import os
import sys
import warnings
import re
import time
from cozepy import COZE_CN_BASE_URL, Coze, TokenAuth, Message
import platform

# 忽略SSL证书警告
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

# ---------------------- 核心配置 ----------------------
WEEK_LIST_API = "https://dgsx.cqvie.edu.cn/prod-api/baseinfo/week/student_list?internshipPlanId=752"
QUERY_SUBMITTED_WEEK_URL = "https://dgsx.cqvie.edu.cn/prod-api/internship_pending/weekrecord/list?pageNum=1&pageSize=100&internshipPlanSemester=12"
SUBMIT_WEEK_URL = "https://dgsx.cqvie.edu.cn/prod-api/internship_pending/weekrecord"
SIGN_QUERY_URL_FOR_ID = "https://dgsx.cqvie.edu.cn/prod-api/internship_pending/signrecord/list?pageNum=1&pageSize=10&internshipPlanSemester=12"
QUERY_DAILY_URL = "https://dgsx.cqvie.edu.cn/prod-api/internship_pending/dailyrecord/list?pageNum=1&pageSize=50&internshipPlanSemester=12"

# Coze配置
TOKEN = ""
BOT_ID = ""
COZE_PROMPT_TEMPLATE = """我的岗位是{job}，请基于本周的所有日报内容，总结生成该周的实习周报，要求如下：
"""
NO_DAILY_CONTENT_PROMPT = "（本周无日报，基于该岗位通用实习内容生成周报）"

# 全局Session
session = requests.Session()
session.verify = False
session.timeout = 20
session.max_redirects = 5

DEFAULT_USER_AGENT = ''
BASE_HEADERS = {
    'User-Agent': DEFAULT_USER_AGENT,
    'Accept': 'application/json, text/plain, */*',
    'Content-Type': 'application/json;charset=UTF-8',
    'Origin': 'https://dgsx.cqvie.edu.cn',
    'Referer': 'https://dgsx.cqvie.edu.cn/internship_pending/weekrecord',
    'X-Requested-With': '',
    'Sec-Fetch-Dest': '',
    'Sec-Fetch-Mode': '',
    'Sec-Fetch-Site': '',
    'Sec-Ch-Ua': '',
    'Sec-Ch-Ua-Mobile': '',
    'Sec-Ch-Ua-Platform': '""',
    'Accept-Encoding': '',
    'Accept-Language': '',
    'Connection': '',
}
session.headers.update(BASE_HEADERS)

# ---------------------- 工具函数 ----------------------
def get_timestamp():
    """对齐day.py，获取时间戳"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def parse_cookies(cookie_str):
    cookies = {}
    for item in cookie_str.strip(';').split(';'):
        item = item.strip()
        if '=' in item:
            k, v = item.split('=', 1)
            cookies[k.strip()] = v.strip()
    return cookies

def init_session(cookie_str, agent_str):
    """对齐day.py的会话初始化逻辑"""
    session.cookies.clear()
    base_cookies = parse_cookies(cookie_str)
    session.cookies.update(base_cookies)
    session.headers.update({'User-Agent': agent_str})

    required_cookies = ['Admin-Token', 'JSESSIONID']
    cookie_dict = {cookie.name: cookie.value for cookie in session.cookies}
    missing = [k for k in required_cookies if k not in cookie_dict or not cookie_dict[k]]
    if missing:
        return False, f"缺少必要Cookie：{missing}"
    return True, ""

def get_distribution_id_dynamically():
    headers = session.headers.copy()
    headers['Authorization'] = f'Bearer {session.cookies.get("Admin-Token", "")}'

    # 尝试从周报接口获取
    try:
        res = session.get(QUERY_SUBMITTED_WEEK_URL, headers=headers, allow_redirects=False)
        if res.status_code == 200:
            res_json = res.json()
            if res_json.get('code') == 200 and res_json.get('rows'):
                first_week = res_json['rows'][0]
                dist_id = first_week.get('distributionId') or first_week.get('signInternshipPlanId')
                if dist_id:
                    return dist_id, ""
    except Exception as e:
        pass

    # 尝试从签到接口获取
    try:
        res = session.get(SIGN_QUERY_URL_FOR_ID, headers=headers, allow_redirects=False)
        if res.status_code == 200:
            res_json = res.json()
            if res_json.get('code') == 200 and res_json.get('rows'):
                return res_json['rows'][0].get('signInternshipPlanId'), ""
    except Exception as e:
        pass

    return , "未动态获取到ID"

def get_week_list_from_api():
    try:
        headers = session.headers.copy()
        headers['Authorization'] = f'Bearer {session.cookies.get("Admin-Token", "")}'

        response = session.get(WEEK_LIST_API, headers=headers, allow_redirects=False)
        if response.status_code != 200:
            return []

        res_json = response.json()
        if res_json.get('code') != 200:
            return []

        valid_week_list = []
        for week in res_json.get('data', []):
            if all([week.get('semesterWeekId'), week.get('weekName'), week.get('startDate'), week.get('endDate')]):
                week_name_clean = re.sub(r'\(.*?\)|（.*?）', '', week.get('weekName')).strip()
                week['weekNameClean'] = week_name_clean
                valid_week_list.append(week)
        return valid_week_list
    except Exception as e:
        return []

def get_week_type(week, today):
    """
    判断周的类型：历史周/当前周/未来周
    返回：history / current / future
    """
    try:
        week_start = datetime.datetime.strptime(week["startDate"], "%Y-%m-%d")
        week_end = datetime.datetime.strptime(week["endDate"], "%Y-%m-%d")
        
        # 未来周：开始时间晚于今天
        if week_start > today:
            return "future"
        # 历史周：结束时间早于今天
        elif week_end < today:
            return "history"
        # 当前周：今天在周范围内
        else:
            return "current"
    except Exception as e:
        return "unknown"

def get_dailies_in_week(week_start, week_end):
    try:
        headers = session.headers.copy()
        headers['Authorization'] = f'Bearer {session.cookies.get("Admin-Token", "")}'

        response = session.get(QUERY_DAILY_URL, headers=headers, allow_redirects=False)
        if response.status_code != 200:
            return NO_DAILY_CONTENT_PROMPT

        res_json = response.json()
        if res_json.get('code') != 200:
            return NO_DAILY_CONTENT_PROMPT

        start_date = datetime.datetime.strptime(week_start, "%Y-%m-%d")
        end_date = datetime.datetime.strptime(week_end, "%Y-%m-%d")
        weekly_dailies = []

        for item in res_json.get('rows', []):
            daily_date = item.get('dailyRecordDate')
            daily_content = item.get('dailyRecordContent')
            if not daily_date or not daily_content:
                continue
            try:
                date = datetime.datetime.strptime(daily_date, "%Y-%m-%d")
                if start_date <= date <= end_date:
                    weekly_dailies.append({"date": daily_date, "content": daily_content})
            except:
                continue

        if not weekly_dailies:
            return NO_DAILY_CONTENT_PROMPT

        weekly_dailies.sort(key=lambda x: x['date'])
        formatted_content = "当周日报：\n"
        for daily in weekly_dailies:
            formatted_content += f"\n{daily['date']}：{daily['content']}\n"
        return formatted_content
    except Exception as e:
        return NO_DAILY_CONTENT_PROMPT

def query_submitted_week_ids():
    try:
        headers = session.headers.copy()
        headers['Authorization'] = f'Bearer {session.cookies.get("Admin-Token", "")}'

        response = session.get(QUERY_SUBMITTED_WEEK_URL, headers=headers, allow_redirects=False)
        if response.status_code != 200:
            return []

        res_json = response.json()
        if res_json.get('code') != 200:
            return []

        submitted_ids = [item.get('semesterWeekId') for item in res_json.get('rows', []) if item.get('semesterWeekId')]
        return submitted_ids
    except Exception as e:
        return []

def clean_coze_output(content):
    if not content:
        return ""

    week_date_patterns = [
        r'根据第\d+周.*?日报内容',
        r'第\d+周\(.*?\)|第\d+周（.*?）',
        r'202\d-\d{2}-\d{2}至202\d-\d{2}-\d{2}',
        r'202\d-\d{2}-\d{2}',
        r'本周\(.*?\)|本周（.*?）'
    ]
    for pattern in week_date_patterns:
        content = re.sub(pattern, '', content, flags=re.DOTALL | re.IGNORECASE)

    # 移除系统字段
    sys_patterns = [
        r'\{"msg_type".*?"from_unit":.*?\}',
        r'"msg_type":"generate_answer_finish".*?',
        r'"finish_reason":\d+.*?"FinData":.*?',
        r'from_module.*?from_unit.*?'
    ]
    for pattern in sys_patterns:
        content = re.sub(pattern, '', content, flags=re.DOTALL | re.IGNORECASE)

    # 清理格式
    content = re.sub(r'\{|\}|\[|\]|\"|\'|,|:', '', content)
    content = re.sub(r'\\n|\\r|\\t', '\n', content)
    content = re.sub(r'\n+', '\n', content)
    content = re.sub(r' +', ' ', content)

    return content.strip()

def coze_chat_week(job, week_name, week_start, week_end):
    weekly_dailies_content = get_dailies_in_week(week_start, week_end)
    final_prompt = COZE_PROMPT_TEMPLATE.format(
        job=job,
        weekly_dailies_content=weekly_dailies_content
    )

    coze = Coze(auth=TokenAuth(token=TOKEN), base_url=COZE_CN_BASE_URL)
    user_id = f"u{int(time.time())}"
    try:
        msg = Message(role="user", content=final_prompt, content_type="text")
        res = coze.chat.create_and_poll(bot_id=BOT_ID, user_id=user_id, additional_messages=[msg])
        reply = [m.content.strip() for m in res.messages if m.role == "assistant"]
        raw_content = "\n".join(reply) if reply else None
        clean_content = clean_coze_output(raw_content)
        return clean_content if clean_content else None
    except Exception as e:
        return None

def submit_single_week(week_info, job, distribution_id, week_type, retry=2):  # 新增 week_type 参数
    semester_week_id = week_info["semesterWeekId"]
    week_name = week_info.get("weekNameClean", week_info["weekName"])
    week_start = week_info["startDate"]
    week_end = week_info["endDate"]

    # 生成周报内容
    week_content = coze_chat_week(job, week_name, week_start, week_end)
    if not week_content:
        return False

    # 核心修改：所有提交都设为"准时提交"（weekRecordType=0），不再区分周类型
    week_record_type = "0"  # 原逻辑："0" if week_type == "current" else "1"
    
    request_data = {
        "distributionId": distribution_id,
        "semesterWeekId": semester_week_id,
        "weekRecordContent": week_content.strip(),
        "weekRecordType": week_record_type  # 动态赋值，不再硬编码
    }

    request_headers = session.headers.copy()
    request_headers['Authorization'] = f'Bearer {session.cookies.get("Admin-Token", "")}'
    request_headers['Content-Type'] = 'application/json;charset=UTF-8'

    # 重试机制
    for attempt in range(retry + 1):
        try:
            # 对齐day.py，使用json参数自动处理编码
            response = session.post(
                SUBMIT_WEEK_URL,
                headers=request_headers,
                json=request_data,
                allow_redirects=False,
                timeout=20
            )

            if response.status_code == 200:
                try:
                    res_json = response.json()
                    if res_json.get('code') in [200, 0] or res_json.get('msg') == "操作成功":
                        return True
                    else:
                        continue
                except json.JSONDecodeError:
                    continue
            elif response.status_code == 401 or response.status_code == 403:
                break
            else:
                if attempt < retry:
                    time.sleep(1)
                else:
                    continue

        except requests.exceptions.Timeout:
            if attempt < retry:
                time.sleep(1)
            else:
                continue
        except requests.exceptions.ConnectionError:
            if attempt < retry:
                time.sleep(1)
            else:
                continue
        except Exception as e:
            if attempt < retry:
                time.sleep(1)
            else:
                continue

    return False

# ---------------------- 主逻辑 ----------------------
def auto_check_and_submit_week(job=None, cookie_str=None, agent_str=None):
    final_result = {
        "timestamp": get_timestamp(),
        "status": "success",
        "msg": ""
    }

    try:
        if job is None and len(sys.argv) > 1:
            job = sys.argv[1]
        if cookie_str is None and len(sys.argv) > 2:
            cookie_str = sys.argv[2]
        if agent_str is None and len(sys.argv) > 3:
            agent_str = sys.argv[3]

        default_agent = DEFAULT_USER_AGENT

        if job is None:
            if platform.system() == 'Windows':
                os.system('chcp 65001 > nul 2>&1')
            print("请输入岗位名称：")
            try:
                job = input("> ").strip()
                if not job:
                    final_result["status"] = "fail"
                    final_result["msg"] = "岗位名称不能为空"
                    print(json.dumps(final_result, ensure_ascii=False))
                    return
            except Exception as e:
                final_result["status"] = "fail"
                final_result["msg"] = "岗位输入异常"
                print(json.dumps(final_result, ensure_ascii=False))
                return

        if cookie_str is None:
            if platform.system() == 'Windows':
                os.system('chcp 65001 > nul 2>&1')
            print("请输入COOKIE：")
            try:
                cookie_str = input("> ").strip()
                if not cookie_str:
                    final_result["status"] = "fail"
                    final_result["msg"] = "Cookie不能为空"
                    print(json.dumps(final_result, ensure_ascii=False))
                    return
            except Exception as e:
                final_result["status"] = "fail"
                final_result["msg"] = "Cookie输入异常"
                print(json.dumps(final_result, ensure_ascii=False))
                return

        if agent_str is None:
            if platform.system() == 'Windows':
                os.system('chcp 65001 > nul 2>&1')
            print(f"请输入User-Agent (留空则使用默认值):\n{default_agent}")
            try:
                agent_input = input("> ").strip()
                agent_str = agent_input if agent_input else default_agent
            except Exception as e:
                agent_str = default_agent

        # 2. 初始化会话，对齐day.py
        init_ok, init_msg = init_session(cookie_str, agent_str)
        if not init_ok:
            final_result["status"] = "fail"
            final_result["msg"] = init_msg
            print(json.dumps(final_result, ensure_ascii=False))
            return

        # 3. 获取distributionId
        time.sleep(0.4)
        distribution_id, dist_msg = get_distribution_id_dynamically()
        if not distribution_id:
            final_result["status"] = "fail"
            final_result["msg"] = "无法获取distributionId，无法继续提交周报"
            print(json.dumps(final_result, ensure_ascii=False))
            return

        # 4. 拉取周列表
        time.sleep(0.4)
        week_list = get_week_list_from_api()
        if not week_list:
            final_result["status"] = "fail"
            final_result["msg"] = "未获取到周列表，无法继续提交周报"
            print(json.dumps(final_result, ensure_ascii=False))
            return

        # 5. 获取已提交的周ID列表
        submitted_ids = query_submitted_week_ids()
        today = datetime.datetime.now()
        today_str = today.strftime("%Y-%m-%d")

        # 6. 遍历处理所有周，汇总结果
        success_weeks = []
        failed_weeks = []
        skipped_weeks = []

        for week in week_list:
            week_type = get_week_type(week, today)
            week_name = week['weekNameClean']
            week_time_range = f"{week['startDate']} - {week['endDate']}"
            week_id = week['semesterWeekId']

            # 跳过无法识别和未来周
            if week_type in ["unknown", "future"]:
                skipped_weeks.append(f"{week_name}")
                continue

            # 跳过已提交的周
            if week_id in submitted_ids:
                skipped_weeks.append(f"{week_name}")
                continue

            # 提交未提交的周
            time.sleep(0.5)
            # 修复：传入 week_type 参数
            if submit_single_week(week, job, distribution_id, week_type):
                success_weeks.append(f"【{week_name}】（{week_time_range}）")
            else:
                failed_weeks.append(f"【{week_name}】（{week_time_range}）")

        # 7. 构造简洁的msg
        if success_weeks and not failed_weeks:
            if len(success_weeks) == 1:
                final_result["msg"] = f"当日（{today_str}）成功提交{success_weeks[0]}的周报"
            else:
                week_str = "、".join(success_weeks)
                final_result["msg"] = f"当日（{today_str}）成功提交{week_str}的周报"
        elif failed_weeks and not success_weeks:
            week_str = "、".join(failed_weeks)
            final_result["status"] = "fail"
            final_result["msg"] = f"当日（{today_str}）提交{week_str}的周报失败"
        elif success_weeks and failed_weeks:
            success_str = "、".join(success_weeks)
            failed_str = "、".join(failed_weeks)
            final_result["status"] = "fail"
            final_result["msg"] = f"当日（{today_str}）成功提交{success_str}的周报，提交{failed_str}的周报失败"
        else:
            final_result["msg"] = f"当日（{today_str}）无需要提交的周报"

    except Exception as e:
        final_result["status"] = "fail"
        final_result["msg"] = f"执行异常：{str(e)[:50]}"

    # 输出简洁JSON格式
    print(json.dumps(final_result, ensure_ascii=False))

if __name__ == '__main__':
    # 对齐day.py，解决Windows中文乱码
    if os.name == 'nt':
        os.system('chcp 65001 > nul 2>&1')

    # 检查依赖，对齐day.py风格
    try:
        from cozepy import Coze, TokenAuth, Message
    except ImportError:
        final_result = {
            "timestamp": get_timestamp(),
            "status": "fail",
            "msg": "缺少cozepy依赖，请执行：pip install cozepy"
        }
        print(json.dumps(final_result, ensure_ascii=False))
        exit(1)

    # 执行主逻辑

    auto_check_and_submit_week()
