"""全链路测试：新增 / 跨群去重 / 变更 / 冲突 / 闲聊过滤 / ics 生成"""
from app.agent import agent

g1, g2, g3 = '数据结构课程群', '机器人社社团群', '宿舍群'

r1 = agent.handle_message(g1, '助教', '【助教】各位同学，数据结构期末考试定在6月10日 14:00，地点在紫金港校区东1楼101教室，请相互转告')
print('① 新增: ', r1['notice']['event'], '|', r1['notice']['time'], '|', r1['notice']['location'], '| 任务:', r1['task']['type'])
print('   确认 →', agent.confirm_task(r1['task']['id'], True)['result'])

r2 = agent.handle_message(g3, '同学', '【同学】提醒一下，数据结构期末考试6月10日 14:00在东1楼101，大家考试周别忘了复习')
print('② 跨群去重:', r2.get('dedup'))

r3 = agent.handle_message(g1, '助教', '【助教】重要通知：数据结构期末考试时间变更为6月12日 14:00，地点不变，请以新时间为准')
print('③ 变更: ', r3['task']['type'], '|', r3['task']['summary'])
print('   确认 →', agent.confirm_task(r3['task']['id'], True)['result'])

r4 = agent.handle_message(g2, '社长', '【社长】重要提醒（转自教务）：数据结构期末考试时间为6月11日 9:00，与此前课程群说的6月10日 14:00不一致，请同学们以教务答复为准')
print('④ 冲突: ', r4['task']['type'], '|', r4['task']['summary'])

r5 = agent.handle_message(g2, '社长', '【社长】机器人社本学期总结会将于6月11日 14:00在南校区活动中心201举行，全体成员参加')
print('⑤ 新增2: ', r5['notice']['event'], '|', r5['notice']['time'], '|', r5['notice']['location'])

r6 = agent.handle_message(g3, '我', '兄弟们今晚开黑吗？')
print('⑥ 闲聊过滤:', r6['recognized'])

print('\n=== calendar.ics ===')
print(open('data/calendar.ics').read())
