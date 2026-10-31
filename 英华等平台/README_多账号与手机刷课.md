# 多账号 / 多平台 / 手机(Termux) 刷课说明

> 版本: aoaostar_mooc 多平台增强版 (基于 v1.3.2 源码修改)
> 现支持 **英华学堂 + 学习通(超星) + 知到(智慧树)** 三个平台混刷,
> 多账号并发、Web 进度面板、Termux 手机部署 全部沿用原有机制。

---

## 一、三个平台怎么配

`config.json` 的 `users` 数组里, 每个账号一个块, 用 `platform` 字段区分平台:

```json
{
  "global": { "server": ":10086", "limit": 4 },
  "users": [
    { "platform": "yinghua",  "base_url": "https://你的英华站点/", "school_id": 0, "username": "英华账号",   "password": "英华密码" },
    { "platform": "chaoxing", "base_url": "https://www.chaoxing.com/", "school_id": 0, "username": "学习通手机号", "password": "学习通密码" },
    { "platform": "zhihuishu", "base_url": "https://www.zhihuishu.com/", "school_id": 0, "username": "知到手机号",  "password": "知到密码" }
  ]
}
```

要点:
- `platform` 留空 = 英华(完全兼容旧配置); 也可不填 platform, 按域名自动识别
- 学习通: 用 **手机号+密码**(和学习通App一致); base_url 随意填, 实际接口走官方域名
- 知到: 密码优先, 若触发滑块验证码会**自动改用扫码**——终端里会出现二维码,
  用知到App扫一下即可; 同一账号整个运行期间只扫一次
- `limit` 是全局并发协程数, 建议 ≥ 账号数
- 学习通视频按 **1:1 真实时长**模拟观看(先尝试直接完成, 不行就每30~90秒心跳);
  知到按 **1.5倍速** 模拟并自动答视频弹题, 每30秒上报一次

配套模板: `config.示例1/2`(英华多账号)、`config.示例3-三平台混刷.json`

## 二、本增强版改了什么

| 改动 | 说明 |
|---|---|
| 多平台架构 | 新增 pkg/platform 抽象层 + pkg/chaoxing + pkg/zhihuishu |
| 多账号容错 | 任一账号登录失败只跳过该账号, 不再终止全局(原版会全体退出) |
| 账号级会话 | 同一账号只登录一次, 所有课程任务复用登录态 |
| 防风控 | 学习通报报限流≥2s/账号; 知到含随机暂停、watchPoint/ev 反检测编码 |

## 三、PC 编译(源码更新后)

双击 `build_windows.bat`(需 Go 1.17+, `winget install GoLang.Go`)。
当前目录的 `aoaostar_mooc.exe` 已是多平台版, `aoaostar_mooc_v1.3.2_原版备份.exe` 是原版。

## 四、手机(Termux)

1. 把最新的 `mooc-main` 源码文件夹 + `termux/` 三个脚本传到手机 Download 目录
2. Termux 执行:
   ```bash
   termux-setup-storage
   bash /sdcard/Download/termux/termux_setup.sh
   ```
   (自动装Go → 编译 → 部署到 ~/mooc; 依赖自动走 goproxy.cn 国内镜像)
3. `nano ~/mooc/config.json` 填账号(同上, 支持 platform 字段)
4. `bash /sdcard/Download/termux/run.sh` 启动; `stop.sh` 停止
5. 手机浏览器开 `http://127.0.0.1:10086` 看进度
- 知到若走扫码: 二维码直接显示在 Termux 终端里, 知到App 扫即可

## 五、实测调试指引(重要)

平台接口来自公开逆向实现, 首次运行请用**真实账号**试跑并观察日志:
1. 学习通: 若看到 `触发风控验证码(403)` → 先用浏览器登录学习通点一遍视频验证, 再重跑
2. 知到: 密码登录失败属预期(平台加了滑块), 看到二维码扫即可
3. 遇到报错把日志(`logs/aoaostar.log` 或控制台)发给协助者即可定位修复

## 六、注意事项

- 仅供个人学习研究, 自动刷课违反平台条款, 有账号风控/成绩作废风险
- 同一账号不要在电脑和手机同时刷(会互相顶登录)
- 熄屏后建议将 Termux 设为电池不优化, 防止进程被杀
