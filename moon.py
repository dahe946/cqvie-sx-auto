import requests
import json
import datetime
import os
import sys
import warnings
import time
import re
from cozepy import Coze, TokenAuth, Message, COZE_CN_BASE_URL
import platform

warnings.filterwarnings('ignore', message='Unverified HTTPS request')

# ---------------------- 核心配置 ----------------------
# 接口配置
MONTH_LIST_API = "https://dgsx.cqvie.edu.cn/prod-api/internship_before/month/list_all?internshipPlanId=752"
QUERY_SUBMITTED_WEEK_URL = "https://dgsx.cqvie.edu.cn/prod-api/internship_pending/weekrecord/list?pageNum=1&pageSize=100&internshipPlanSemester=12"
MONTH_QUERY_URL = "https://dgsx.cqvie.edu.cn/prod-api/internship_pending/monthrecord/list?pageNum=1&pageSize=50&internshipPlanSemester=12"
MONTH_SUBMIT_URL = "https://dgsx.cqvie.edu.cn/prod-api/internship_pending/monthrecord"

# Coze配置
COZE_TOKEN = ""
COZE_BOT_ID = ""

# 模板配置
MONTH_PROMPT_TEMPLATE = """我的岗位是{job}，请基于{month}的所有实习周报内容，总结生成该月的实习月报总结，要求如下：
"""

# 默认User-Agent
DEFAULT_USER_AGENT = ''

# 初始化Session
session = requests.Session()
session.verify = False
session.timeout = 15
session.max_redirects = 5
session.headers.update({
    'User-Agent': DEFAULT_USER_AGENT,
    'Accept': 'application/json, text/plain, */*',
    'Origin': 'https://dgsx.cqvie.edu.cn',
    'Referer': 'https://dgsx.cqvie.edu.cn/internship_pending/monthrecord',
    'Content-Type': 'application/json;charset=UTF-8'
})


# ---------------------- 工具函数 ----------------------
def get_timestamp():
    """获取统一格式的时间戳"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_cookies(cookie_str):
    cookies = {}
    items = cookie_str.strip(';').split(';')
    for item in items:
        if '=' in item:
            k, *v_parts = item.split('=')
            k = k.strip()
            v = '='.join(v_parts).strip()
            cookies[k] = v
    return cookies


def clean_coze_content(content):
    """清洗Coze返回的内容"""
    if not content:
        return ""

    # 移除系统字段和冗余符号
    sys_patterns = [
        r'from_module:\w*?from_unit:\w*?',
        r'from_module:null',
        r'from_unit:null',
        r'from_module',
        r'from_unit',
        r'msg_type:\w*?',
        r'finish_reason:\d*?',
        r'FinData:\w*?',
        r'msg_type',
        r'finish_reason',
        r'FinData'
    ]
    for pattern in sys_patterns:
        content = re.sub(pattern, '', content, flags=re.IGNORECASE)

    content = re.sub(r'\{[\s\S]*?\}', '', content)
    content = re.sub(r'\[[\s\S]*?\]', '', content)
    content = re.sub(r'null|undefined', '', content, flags=re.IGNORECASE)
    content = re.sub(r',+|"|\'|;+|\}+$', '', content)
    content = re.sub(r'\s+', ' ', content)
    return content.strip()


def is_month_future(month_start_date):
    current_date = datetime.datetime.now().date()
    try:
        start_date = datetime.datetime.strptime(month_start_date, "%Y-%m-%d").date()
        current_month_first = datetime.date(current_date.year, current_date.month, 1)
        month_first = datetime.date(start_date.year, start_date.month, 1)
        return month_first > current_month_first
    except:
        return True  # 解析失败认为是未来/不确定


def is_month_ended(month_end_date_str):
    if not month_end_date_str:
        return False
    try:
        current_date = datetime.datetime.now().date()
        # 兼容处理可能带时间戳的结束日期
        month_end_date = datetime.datetime.strptime(month_end_date_str.split(' ')[0], "%Y-%m-%d").date()
        return current_date >= month_end_date
    except:
        return False


def init_session(cookie_str, agent_str):
    """初始化会话：设置Cookie、User-Agent和Authorization头"""
    try:
        session.cookies.clear()
        session.cookies.update(parse_cookies(cookie_str))

        # 更新User-Agent
        session.headers.update({'User-Agent': agent_str})

        required_cookies = ['Admin-Token', 'JSESSIONID']
        cookie_dict = {k: v for k, v in session.cookies.items()}
        missing = [k for k in required_cookies if k not in cookie_dict]
        if missing:
            return False, f"缺少Cookie：{','.join(missing)}"

        # 设置Authorization头
        session.headers['Authorization'] = f'Bearer {cookie_dict.get("Admin-Token", "")}'
        return True, ""
    except Exception as e:
        return False, f"会话初始化失败：{str(e)}"


def get_month_list():
    month_map = {}
    skipped_months = []
    try:
        res = session.get(MONTH_LIST_API, allow_redirects=False, timeout=10)
        if res.status_code != 200:
            return None, [], f"获取月份列表API状态码异常：{res.status_code}"

        res_json = res.json()
        if res_json.get('code') != 200 or not res_json.get('data'):
            return None, [], f"获取月份列表API返回异常：{res_json.get('msg', '未知错误')}"

        for item in res_json['data']:
            month_name = item.get('monthName')
            start_date_str = item.get('startDate')
            end_date_str = item.get('endDate')

            if not month_name or not start_date_str:
                continue

            if is_month_future(start_date_str):
                skipped_months.append(f"{month_name} (未来月)")
                continue

            if not is_month_ended(end_date_str):
                skipped_months.append(f"{month_name} (未结束)")
                continue

            month_map[month_name] = {
                'semesterMonthId': item.get('semesterMonthId'),
                'startDate': start_date_str,
                'endDate': end_date_str,
                'internshipPlanId': item.get('internshipPlanId', 752)
            }

        return month_map, skipped_months, ""
    except Exception as e:
        return None, [], f"获取月份列表失败：{str(e)}"


def get_distribution_id():
    try:
        res = session.get(MONTH_QUERY_URL, allow_redirects=False, timeout=10)
        if res.status_code == 200:
            res_json = res.json()
            if res_json.get('code') == 200 and res_json.get('rows'):
                # 尝试从月报记录中获取
                return res_json['rows'][0].get('distributionId')
        return
    except:
        return 


def check_month_exist(month_str):
    try:
        res = session.get(MONTH_QUERY_URL, allow_redirects=False, timeout=10)
        if res.status_code != 200:
            return None

        res_json = res.json()
        for row in res_json.get('rows', []):
            if row.get('monthName') == month_str:
                return True
        return False
    except:
        return None


def get_weekly_content(month_str, month_map):
    """获取指定月份下的所有已提交周报内容"""
    if month_str not in month_map:
        return ""
    month_info = month_map[month_str]
    try:
        res = session.get(QUERY_SUBMITTED_WEEK_URL, allow_redirects=False, timeout=10)
        if res.status_code != 200:
            return ""

        res_json = res.json()
        weekly_content = ""
        start_dt = datetime.datetime.strptime(month_info['startDate'], "%Y-%m-%d")
        end_dt = datetime.datetime.strptime(month_info['endDate'].split(' ')[0], "%Y-%m-%d")

        for row in res_json.get('rows', []):
            # 使用周报的创建时间或开始日期来判断是否在月范围内
            week_dt_str = row.get('createTime', '') or row.get('weekStartDate', '')
            if not week_dt_str:
                continue
            try:
                # 只取日期部分进行比较
                week_dt = datetime.datetime.strptime(week_dt_str.split(' ')[0], "%Y-%m-%d")
                if start_dt <= week_dt <= end_dt:
                    week_content = row.get('weekRecordContent', '')
                    weekly_content += f"\n{row.get('weekName', '某周')}：{week_content}"
            except:
                continue

        return weekly_content.strip()
    except:
        return ""


def generate_month_report(job, month_str, weekly_content):
    """调用Coze生成月报内容"""
    try:
        if not job or not month_str:
            raise ValueError("岗位/月份参数为空")

        final_prompt = MONTH_PROMPT_TEMPLATE.format(
            job=job.strip(),
            month=month_str.strip(),
            weekly_content=weekly_content.strip() or "（当月无周报内容，请基于岗位信息总结）"
        )

        coze = Coze(auth=TokenAuth(token=COZE_TOKEN), base_url=COZE_CN_BASE_URL)
        msg = Message(role="user", content=final_prompt, content_type="text")
        res = coze.chat.create_and_poll(
            bot_id=COZE_BOT_ID,
            user_id=f"auto_{int(time.time())}",
            additional_messages=[msg]
        )

        # 聚合回复并清洗
        raw_content = "\n".join([m.content.strip() for m in res.messages if m.role == "assistant" and m.content])
        month_content = clean_coze_content(raw_content)

        return month_content if len(month_content) >= 50 else None

    except Exception as e:
        return None


def submit_month_report(month_str, month_content, month_map, distribution_id):
    if month_str not in month_map or not month_content:
        return False
    try:
        # 解析年月
        year_match = re.search(r'(\d{4})年', month_str)
        month_match = re.search(r'(\d{1,2})月', month_str)

        year = year_match.group(1) if year_match else str(datetime.datetime.now().year)
        month = month_match.group(1) if month_match else str(datetime.datetime.now().month)

        request_data = {
            "distributionId": distribution_id,
            "year": year,
            "month": month,
            "monthRecordContent": month_content.strip(),
            "monthRecordType": "1",
            "monthWeekId": month_map[month_str]['semesterMonthId'],
            "internshipPlanId": month_map[month_str]['internshipPlanId'],
            "monthName": month_str
        }

        res = session.post(
            MONTH_SUBMIT_URL,
            data=json.dumps(request_data, ensure_ascii=False),
            allow_redirects=False,
            timeout=15
        )
        res_json = res.json()
        return res.status_code == 200 and (
                res_json.get('code') in [0, 200] or res_json.get('msg') in ["操作成功", "提交成功"])
    except Exception as e:
        return False


def auto_fill_month_report(job=None, cookie_str=None, agent_str=None):
    # 初始化最终结果
    final_result = {
        "timestamp": get_timestamp(),
        "status": "success",
        "msg": "",
        "success": 0,
        "skipped": 0,
        "failed": 0,
        "skipped_months": []
    }

    try:
        # 1. 获取用户输入（优先从命令行参数获取）
        if job is None and len(sys.argv) > 1:
            job = sys.argv[1]
        if cookie_str is None and len(sys.argv) > 2:
            cookie_str = sys.argv[2]
        if agent_str is None and len(sys.argv) > 3:
            agent_str = sys.argv[3]

        default_agent = DEFAULT_USER_AGENT

        # 命令行未提供时，使用input()获取
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
            except:
                final_result["status"] = "fail"
                final_result["msg"] = "Cookie输入异常"
                print(json.dumps(final_result, ensure_ascii=False))
                return

        if job is None:
            print("请输入岗位信息：")
            try:
                job = input("> ").strip()
                if not job:
                    final_result["status"] = "fail"
                    final_result["msg"] = "岗位信息不能为空"
                    print(json.dumps(final_result, ensure_ascii=False))
                    return
            except:
                final_result["status"] = "fail"
                final_result["msg"] = "岗位输入异常"
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

        # 3. 获取可处理月份
        month_map, skipped_months, month_err = get_month_list()
        if not month_map and month_err:
            final_result["status"] = "fail"
            final_result["msg"] = month_err
            print(json.dumps(final_result, ensure_ascii=False))
            return

        # 4. 初始化统计数据
        success_num = 0
        skipped_num = len(skipped_months)
        failed_num = 0
        did = get_distribution_id()
        final_result["skipped_months"] = skipped_months

        # 5. 处理每个月份
        if month_map:
            for month in month_map.keys():
                time.sleep(0.3)
                # 检查月报是否已提交
                exist = check_month_exist(month)
                if exist is None:
                    failed_num += 1
                    continue
                if exist:
                    skipped_num += 1
                    continue

                # 获取周报内容并生成月报
                weekly_content = get_weekly_content(month, month_map)
                month_content = generate_month_report(job, month, weekly_content)
                if not month_content:
                    failed_num += 1
                    continue

                # 提交月报
                if submit_month_report(month, month_content, month_map, did):
                    success_num += 1
                else:
                    failed_num += 1

        # 6. 构造最终结果
        final_result["success"] = success_num
        final_result["skipped"] = skipped_num
        final_result["failed"] = failed_num
        
        if failed_num > 0:
            final_result["status"] = "fail"
            final_result["msg"] = f"处理完成：成功{success_num}个，跳过{skipped_num}个，失败{failed_num}个"
        else:
            final_result["msg"] = f"处理完成：成功{success_num}个，跳过{skipped_num}个，失败{failed_num}个"

    except Exception as e:
        final_result["status"] = "fail"
        final_result["msg"] = f"执行异常：{str(e)[:50]}"

    # 输出最终一行JSON
    print(json.dumps(final_result, ensure_ascii=False))


if __name__ == '__main__':
    if os.name == 'nt':
        os.system('chcp 65001 > nul 2>&1')

    try:
        from cozepy import Coze, TokenAuth, Message
    except ImportError:
        final_result = {
            "timestamp": get_timestamp(),
            "status": "fail",
            "msg": "缺少cozepy依赖，请执行：pip install cozepy",
            "success": 0,
            "skipped": 0,
            "failed": 0,
            "skipped_months": []
        }
        print(json.dumps(final_result, ensure_ascii=False))
        exit(1)

    try:
        auto_fill_month_report()
    except KeyboardInterrupt:
        final_result = {
            "timestamp": get_timestamp(),
            "status": "fail",
            "msg": "用户终止操作",
            "success": 0,
            "skipped": 0,
            "failed": 0,
            "skipped_months": []
        }

        print(json.dumps(final_result, ensure_ascii=False))
