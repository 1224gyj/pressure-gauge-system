/* 当前仅保留摄像头测试表；真实识别记录通过实时接口加载。 */
const AREAS = { A: '一号泵站', B: '二号泵站' };
const CONFIG = { warnCoef: 0.5, alarmCoef: 1.0 };
const ACTIVE_GAUGE_IDS = ['YB-106'];
let GAUGES = [
  {
    "id": "YB-106",
    "name": "测试压力表",
    "area": "A",
    "pos": "摄像头测试点",
    "type": "压力表",
    "range": 0.016,
    "acc": 2.5,
    "status": "active",
    "installDate": "未填写"
  }
];
// 变送器尚未接入，不生成模拟读数或比较结果。
let READINGS = [];
let USERS = [
  { acc:'admin',    name:'系统管理员', role:'admin', area:null, status:'启用', lastLogin:'2026-10-04 07:12' },
  { acc:'zhangwei', name:'张伟',      role:'user',  area:'A',  status:'启用', lastLogin:'2026-10-04 08:40' },
  { acc:'liuyang',  name:'刘洋',      role:'user',  area:'B',  status:'启用', lastLogin:'2026-10-04 09:03' },
  { acc:'chenjing', name:'陈静',      role:'user',  area:'B',  status:'停用', lastLogin:'2026-09-27 16:44' },
];
let DEL_REQUESTS = [];
let HANDLES = {};
let AUDIT = [];
let IMPORT_LOG = [];
let LAST_IMPORT = { ts: '', file: '尚未导入整点比较数据' };
