# SlideCap

本地相机幻灯片采集工具：屏幕检测、曝光调节、自动抓拍、透视校正、去重与 PPTX / PDF / PNG 导出。不录音、不录视频，照片保存在本机。

## 快速开始

需要 Python 3.11+、Node.js 20.19+ 或 22.12+，推荐桌面 Chrome。

```sh
python3 -m venv .venv
source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
npm ci --prefix frontend
npm run build --prefix frontend
python run.py
```

打开 http://localhost:8765。首次安装需联网，运行时不依赖云服务。

## 使用

1. 连接相机，拖动四角选定屏幕，点击「确认区域」。
2. 选择曝光模式，点击「开始捕捉」。空格暂停/继续，S 强制拍摄。
3. 结束后检查页面，可删除、排序、合并和更换候选帧。
4. 导出 PPTX、PDF 或 PNG ZIP；导出的幻灯片为图片，不含可编辑文字。

曝光默认「屏幕优先」：每 0.5 秒测量区域亮度 P99，优先调曝光补偿，其次曝光时间，目标 230–240；连续三次达标后尝试 manual 锁定。自动模式使用 continuous；手动模式提供相机支持的滑杆，曝光时间优先吸附到 10ms 整数倍。直方图和红色斑马纹提示亮度及过曝。参数按相机名保存到浏览器本地存储。

曝光能力取决于相机、驱动和浏览器；未开放时隐藏控件。无法锁定或达到目标时显示原因。曝光变化通过 WebSocket 通知后端，收到确认后恢复采集；之后两帧不自动判断换页并重置参考图，强制拍摄仍可用。

运行中请保持页面打开；系统休眠、浏览器后台节流和相机断连可能造成采集间隔。四角识别可能受灯光影响，请人工确认。

## 开发与测试

```sh
python run.py --no-browser
# 另一终端：
npm run dev --prefix frontend

npm run build --prefix frontend
python -m pytest -q

# 可选合成回归
python prototype/make_synthetic.py .test-data/synthetic 0
SLIDECAP_SYNTHETIC=.test-data/synthetic python -m pytest -q tests/test_regression.py
```

前端开发端口为 5173，代理到后端 8765。修改 Python 后重启服务。API 文档：http://localhost:8765/docs。

## 目录与数据

- `frontend/`：React / TypeScript 相机预览与控制。
- `backend/`：FastAPI 服务、会话管理、导出。
- `prototype/`：采集门控、图像处理及合成素材生成。
- `tests/`、`scripts/`：回归测试与诊断工具。
- `sessions/`：本地照片、会话和导出；不会加入版本控制。

可通过 `SLIDECAP_DATA` 指定数据目录。服务仅监听本机，无账号认证，不应直接暴露到公网。发布代码时不要附带会话照片、真实样本、`.env` 或本地缓存。

## 许可证

项目代码采用 [MIT](LICENSE)。合成测试使用的 DejaVu 字体保留其独立[许可证](prototype/fonts/LICENSE)。Python / npm 依赖遵循各自许可证。
