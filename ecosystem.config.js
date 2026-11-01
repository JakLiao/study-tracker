// ====== study-tracker · PM2 进程配置 ======
//
// 为什么参数要进版本库, 而不是写在 pm2 start 的命令行里:
// 这些值决定"数据库晚启动时服务能不能自愈", 属于服务契约的一部分。
// 写在命令行就只活在某个人的 shell 历史中, 换台机器、换个人接手就丢了 ——
// 而这个项目是个人业余维护, 恰恰最怕"只有原作者知道为什么这么配"。
//
//   用法: pm2 start ecosystem.config.js
//
// 下面是 PM2 默认值的逐项改动, 每条都对应一个真实故障:
//
//   max_restarts 15 → 50
//       PM2 默认连续失败 15 次就**永久放弃**。实测默认配置下这 15 次重试
//       在 2 秒内烧光 —— 数据库拒连是瞬时的, 进程 1 秒内就死 —— 之后根因
//       修好也不会自己起来 (容器 08:53 就绪, PM2 到 08:55 仍是 errored)。
//       50 次给"容器起得慢"留足余量。
//
//   restart_delay 0 → 5000
//       默认 0 = 失败后立刻重试, 等于几秒内把预算一次撞光。留 5s 空档。
//
//   exp_backoff_restart_delay 0 → 30000
//       连续失败时延迟翻倍, 封顶 30s。真的长时间不可用时, 退避比硬撞
//       更快也更省电。
//
//   min_uptime 1000 → 10000
//       关键参数: 进程活过 min_uptime 才算"这次重启成功", 重启计数清零。
//       默认 1s 太宽松 —— 数据库卡在连接超时上时进程能活过 1s, 于是每次
//       都被算成"成功", 可服务一个请求都处理不了。抬到 10s, 让"假活"
//       也算失败。
//
//   autorestart true
//       进程退出后自动拉起 (默认值, 写出来是为了让这份契约自解释)。
//
//   time true
//       PM2 日志带时间戳, 排查"几点崩的"用。
//
// 注意: 这些只是兜底。真正让服务扛住"数据库晚启动"的是 pm2-start.sh 里的
// 就绪等待 (scripts/wait_for_db.py) —— 进程活着等数据库, 就永远走不到
// 重启路径, PM2 也永远不会判定失败。

module.exports = {
  apps: [
    {
      name: "study-tracker",
      script: "./pm2-start.sh",
      interpreter: "bash",
      cwd: __dirname,

      autorestart: true,
      max_restarts: 50,
      restart_delay: 5000,
      exp_backoff_restart_delay: 30000,
      exp_backoff_restart_on_failure: true,
      min_uptime: 10000,

      time: true,
      watch: false,
    },
  ],
};
