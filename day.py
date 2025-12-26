import requests
import json
import datetime
import os
import sys
import warnings
import time
import re
from datetime import timedelta
from cozepy import COZE_CN_BASE_URL, Coze, TokenAuth, Message
import platform  # 确保平台模块已导入

# 忽略SSL证书警告
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

# ---------------------- 核心配置 ----------------------
# 固定时间范围：2025-11-17 至 2026-01-25
START_DATE_STR = "2025-11-17"
END_DATE_STR = "2026-01-25"
START_DATE = datetime.datetime.strptime(START_DATE_STR, "%Y-%m-%d")
END_DATE = datetime.datetime.strptime(END_DATE_STR, "%Y-%m-%d")

# Coze配置（新增连续性提示）
TOKEN = "pat_ABTxMqlmlUQewjT7SJXhfD5nvXsTHvOsPhjV9GVGU5bD6LgK4pwSuLnELdQrWg5a"
BOT_ID = "7577689412885725236"
# 基础prompt，新增前序内容占位符
COZE_PROMPT_TEMPLATE = """我的岗位是{job}，请生成今日实习日报，要求如下：
1. 必须延续前一日的工作内容，保持业务连贯性，但不得模板化，体现灵活性，一定要保持句子完整性；
2. 禁止生成无关内容、无关名字、具体时间/日期、特殊符号、数字序号、emoji、注释；
3. 内容要有换行，标点符号，纯文本格式；
4. 字数50-100字
{pre_content_prompt}"""
# 无前置内容时的兜底提示
NO_PRE_CONTENT_PROMPT = "（首次提交，直接生成当日实习工作内容即可）"

# 接口配置
QUERY_LIST_URL = "https://dgsx.cqvie.edu.cn/prod-api/internship_pending/dailyrecord/list?pageNum=1&pageSize=50&internshipPlanSemester=12"
SUBMIT_DAILY_URL = "https://dgsx.cqvie.edu.cn/prod-api/internship_pending/dailyrecord"
SIGN_QUERY_URL_FOR_ID = "https://dgsx.cqvie.edu.cn/prod-api/internship_pending/signrecord/list?pageNum=1&pageSize=10&internshipPlanSemester=12"

# 全局Session
session = requests.Session()
session.verify = False
session.timeout = 20
session.max_redirects = 5
# 基础Headers，User-Agent 将在 init_session 中被用户输入覆盖
BASE_HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Encoding': 'gzip, deflate, br, zstd',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Content-Type': 'application/json;charset=UTF-8',  # 明确指定UTF-8编码
    'Origin': 'https://dgsx.cqvie.edu.cn',
    'Referer': 'https://dgsx.cqvie.edu.cn/internship_pending/dailyrecord',
    'Sec-Ch-Ua': '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'Connection': 'keep-alive',
}
# 默认 User-Agent，用于初始化和作为默认值
DEFAULT_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36'
BASE_HEADERS['User-Agent'] = DEFAULT_USER_AGENT
session.headers.update(BASE_HEADERS)

LEGAL_HOLIDAYS = []


# ---------------------- 工具函数 ----------------------
def get_timestamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_cookies(cookie_str):
    cookies = {}
    for item in cookie_str.strip(';').split(';'):
        if '=' in item:
            k, v = item.split('=', 1)
            cookies[k.strip()] = v.strip()
    return cookies


def init_session(cookie_str, agent_str):
    """
    初始化会话：设置Cookie和User-Agent。
    """
    session.cookies.clear()
    base_cookies = parse_cookies(cookie_str)
    session.cookies.update(base_cookies)

    # 关键：用用户提供的 Agent 覆盖默认值
    session.headers.update({'User-Agent': agent_str})

    required_cookies = ['Admin-Token', 'mysec_sign_cookie', 'JSESSIONID']
    cookie_dict = {cookie.name: cookie.value for cookie in session.cookies}
    missing = [k for k in required_cookies if k not in cookie_dict or not cookie_dict[k]]
    if missing:
        return False, f"缺少必要Cookie：{missing}"
    return True, ""


def get_distribution_id_dynamically():
    """动态获取distributionId"""
    headers = session.headers.copy()
    headers['Authorization'] = f'Bearer {session.cookies.get("Admin-Token", "")}'
    headers.pop('Accept-Encoding', None)

    # 1. 优先从日报接口获取
    try:
        res = session.get(QUERY_LIST_URL, headers=headers, allow_redirects=False)
        if res.status_code == 200:
            res_json = res.json()
            if res_json.get('code') == 200 and res_json.get('rows'):
                first_daily = res_json['rows'][0]
                dist_id = first_daily.get('distributionId') or first_daily.get('signInternshipPlanId')
                if dist_id:
                    return dist_id, ""
    except Exception as e:
        pass  # 忽略错误，尝试下一个接口

    # 2. 兜底从签到接口获取
    try:
        res = session.get(SIGN_QUERY_URL_FOR_ID, headers=headers, allow_redirects=False)
        if res.status_code == 200:
            res_json = res.json()
            if res_json.get('code') == 200 and res_json.get('rows'):
                return res_json['rows'][0].get('signInternshipPlanId'), ""
    except Exception as e:
        pass  # 忽略错误，返回默认值

    return 70447, "未动态获取到ID，使用默认值70447"


def is_in_time_range(date_str):
    """判断日期是否在指定区间内"""
    try:
        date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return START_DATE <= date <= END_DATE
    except:
        return False


def is_workday(date_str):
    """判断是否为有效工作日"""
    if not is_in_time_range(date_str):
        return False
    date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    if date.weekday() in [5, 6]:
        return False
    if date_str in LEGAL_HOLIDAYS:
        return False
    return True


def get_latest_submitted_daily():
    """获取最新提交的日报（日期+内容），用于保证连续性"""
    try:
        headers = session.headers.copy()
        headers['Authorization'] = f'Bearer {session.cookies.get("Admin-Token", "")}'
        headers.pop('Accept-Encoding', None)

        response = session.get(QUERY_LIST_URL, headers=headers, allow_redirects=False)
        if response.status_code != 200:
            return None, None

        res_json = response.json()
        if res_json.get('code') != 200:
            return None, None

        # 筛选区间内的日报并按日期降序排序，取最新一条
        valid_dailies = []
        for item in res_json.get('rows', []):
            date_str = item.get('dailyRecordDate')
            content = item.get('dailyRecordContent')
            if date_str and content and is_in_time_range(date_str):
                valid_dailies.append({
                    "date": date_str,
                    "content": content
                })

        if not valid_dailies:
            return None, None

        # 按日期降序排序，取第一条（最新的）
        valid_dailies.sort(key=lambda x: x['date'], reverse=True)
        latest = valid_dailies[0]
        return latest['date'], latest['content']
    except Exception as e:
        return None, None


def query_submitted_dates():
    """查询固定区间内已提交的日报日期"""
    try:
        headers = session.headers.copy()
        headers['Authorization'] = f'Bearer {session.cookies.get("Admin-Token", "")}'
        headers.pop('Accept-Encoding', None)

        response = session.get(QUERY_LIST_URL, headers=headers, allow_redirects=False)
        if response.status_code != 200:
            return []

        res_json = response.json()
        if res_json.get('code') != 200:
            return []

        submitted_dates = []
        for item in res_json.get('rows', []):
            date_str = item.get('dailyRecordDate')
            if date_str and is_in_time_range(date_str):
                submitted_dates.append(date_str)
        return submitted_dates
    except Exception as e:
        return []


def coze_chat(job, latest_content=None):
    """调用Coze生成连贯的日报内容"""
    # 构造带连续性的prompt
    if latest_content:
        pre_content_prompt = f"前一日（{latest_content['date']}）的工作内容如下：\n{latest_content['content']}\n请基于此内容延续生成今日工作日报，体现工作的连贯性和递进性。"
    else:
        pre_content_prompt = NO_PRE_CONTENT_PROMPT

    final_prompt = COZE_PROMPT_TEMPLATE.format(job=job, pre_content_prompt=pre_content_prompt)

    coze = Coze(auth=TokenAuth(token=TOKEN), base_url=COZE_CN_BASE_URL)
    user_id = f"u{int(time.time())}"
    try:
        msg = Message(role="user", content=final_prompt, content_type="text")
        res = coze.chat.create_and_poll(bot_id=BOT_ID, user_id=user_id, additional_messages=[msg])
        reply = [m.content.strip() for m in res.messages if m.role == "assistant" and "msg_type" not in m.content]
        return "\n".join(reply) if reply else None
    except Exception as e:
        return None


def submit_single_daily(date_str, job, distribution_id, latest_daily=None, is_today=False):
    """
    提交带连续性的日报
    返回值：(是否成功, 生成的日报内容)
    """
    if not is_workday(date_str):
        return False, None

    # 生成连贯的日报内容
    daily_content = coze_chat(job, latest_daily)
    if not daily_content:
        return False, None

    # 构造提交数据（直接用字典，无需手动序列化）
    request_data = {
        "distributionId": distribution_id,
        "dailyRecordDate": date_str,
        "dailyRecordContent": daily_content.strip()
    }

    # 构造请求头
    cookie_dict = {cookie.name: cookie.value for cookie in session.cookies}
    request_headers = session.headers.copy()
    request_headers['Authorization'] = f'Bearer {cookie_dict.get("Admin-Token", "")}'
    request_headers.pop('Accept-Encoding', None)

    # 提交请求（核心修改：使用json参数替代data参数）
    try:
        response = session.post(
            SUBMIT_DAILY_URL,
            headers=request_headers,
            json=request_data,  # requests自动序列化+UTF-8编码
            allow_redirects=False,
            timeout=20
        )
    except Exception as e:
        return False, daily_content

    # 处理响应
    try:
        if response.status_code == 200:
            res_json = response.json()
            if res_json.get('code') in [200, 0]:
                return True, daily_content
    except json.JSONDecodeError:
        pass
    except Exception as e:
        pass
    return False, daily_content


def get_workdays_in_range(exclude_today=False):
    """获取【固定区间+近一个月】的有效工作日"""
    today = datetime.datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    one_month_ago = today - timedelta(days=30)

    # 确保开始日期不早于固定开始日期
    actual_start = max(START_DATE, one_month_ago)
    actual_end = min(END_DATE, today)

    workdays = []
    current = actual_start
    while current <= actual_end:
        date_str = current.strftime("%Y-%m-%d")
        if exclude_today and date_str == today_str:
            current += timedelta(days=1)
            continue
        if is_workday(date_str):
            workdays.append(date_str)
        current += timedelta(days=1)
    return workdays


# ---------------------- 主逻辑 ----------------------
def auto_check_and_submit(job=None, cookie_str=None, agent_str=None):
    # 初始化最终结果
    final_result = {
        "timestamp": get_timestamp(),
        "status": "success",
        "msg": ""
    }

    try:
        # 1. 获取用户输入（优先从命令行参数获取）
        if job is None and len(sys.argv) > 1:
            job = sys.argv[1]
        if cookie_str is None and len(sys.argv) > 2:
            cookie_str = sys.argv[2]
        if agent_str is None and len(sys.argv) > 3:
            agent_str = sys.argv[3]

        # 获取默认 Agent 字符串
        default_agent = DEFAULT_USER_AGENT

        # 如果命令行参数没有提供，则使用input()获取
        if job is None:
            if platform.system() == 'Windows':
                os.system('chcp 65001 > nul 2>&1')
            print("请输入你的岗位名称：")
            try:
                job = input("> ").strip()
                if not job:
                    final_result["status"] = "fail"
                    final_result["msg"] = "岗位名称不能为空"
                    print(json.dumps(final_result, ensure_ascii=False))
                    return
            except:
                final_result["status"] = "fail"
                final_result["msg"] = "岗位输入异常"
                print(json.dumps(final_result, ensure_ascii=False))
                return

        if cookie_str is None:
            print("请输入完整的COOKIE_STR字符串：")
            try:
                cookie_str = input("> ").strip()
                if not cookie_str:
                    final_result["status"] = "fail"
                    final_result["msg"] = "Cookie不能为空"
                    print(json.dumps(final_result, ensure_ascii=False))
                    return
            except:
                final_result["status"] = "fail"
                final_result["msg"] = "Cookie输入异常"
                print(json.dumps(final_result, ensure_ascii=False))
                return

        if agent_str is None:
            print(f"请输入User-Agent (留空则使用默认值):\n{default_agent}")
            try:
                agent_input = input("> ").strip()
                agent_str = agent_input if agent_input else default_agent
            except:
                agent_str = default_agent

        # 2. 初始化会话
        init_ok, init_msg = init_session(cookie_str, agent_str)
        if not init_ok:
            final_result["status"] = "fail"
            final_result["msg"] = init_msg
            print(json.dumps(final_result, ensure_ascii=False))
            return

        # 3. 动态获取distributionId
        distribution_id, dist_msg = get_distribution_id_dynamically()

        # 4. 获取最新提交的日报（用于连续性生成）
        latest_date, latest_content = get_latest_submitted_daily()
        latest_daily = None
        if latest_date and latest_content:
            latest_daily = {"date": latest_date, "content": latest_content}

        # 5. 优先处理当日日报 ----------------------
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        today_submit_success = False
        if is_in_time_range(today_str) and is_workday(today_str):
            submitted_dates = query_submitted_dates()
            if today_str not in submitted_dates:
                # 提交当日日报（传入历史内容保证连续性）
                submit_success, submit_content = submit_single_daily(today_str, job, distribution_id, latest_daily, is_today=True)
                if submit_success:
                    today_submit_success = True
                    latest_daily = {"date": today_str, "content": submit_content}
                else:
                    final_result["status"] = "fail"
                    final_result["msg"] = f"当日（{today_str}）日报提交失败"
                    print(json.dumps(final_result, ensure_ascii=False))
                    return

        # 6. 处理近一个月遗漏日报（按日期顺序生成，保证递进性） ----------------------
        workdays = get_workdays_in_range(exclude_today=True)
        submitted_dates = query_submitted_dates()
        unsubmitted_dates = [d for d in workdays if d not in submitted_dates]
        
        success_count = 0
        fail_count = 0
        current_latest = latest_daily  # 迭代更新最新内容，保证补提的日报也有连续性
        if unsubmitted_dates:
            unsubmitted_dates.sort()
            for date_str in unsubmitted_dates:
                time.sleep(1)
                submit_success, submit_content = submit_single_daily(date_str, job, distribution_id, current_latest)
                if submit_success:
                    success_count += 1
                    current_latest = {"date": date_str, "content": submit_content}
                else:
                    fail_count += 1
                    final_result["status"] = "fail"
                    final_result["msg"] = f"{date_str}日报提交失败，补提中断"
                    print(json.dumps(final_result, ensure_ascii=False))
                    return

        # 7. 生成最终消息
        if today_submit_success:
            final_result["msg"] += f"当日（{today_str}）日报提交成功；"
        else:
            final_result["msg"] += f"当日（{today_str}）日报无需提交；"
        
        if unsubmitted_dates:
            final_result["msg"] += f"补提完成：共需补提{len(unsubmitted_dates)}天，成功{success_count}天，失败{fail_count}天"
        else:
            final_result["msg"] += "区间内近一个月有效工作日日报已全部提交，无遗漏"

    except Exception as e:
        final_result["status"] = "fail"
        final_result["msg"] = f"执行异常：{str(e)[:50]}"

    # 输出最终一行JSON
    print(json.dumps(final_result, ensure_ascii=False))


if __name__ == '__main__':
    # 解决Windows中文乱码
    if os.name == 'nt':
        os.system('chcp 65001 > nul 2>&1')

    # 检查依赖
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
    auto_check_and_submit()