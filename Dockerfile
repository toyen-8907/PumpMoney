# 使用 Python 3.9 作為基礎映像
FROM python:3.9

# 設置工作目錄
WORKDIR /app

# 複製當前目錄的內容到容器內部
COPY . .

# 安裝必要的 Python 套件
RUN pip install --no-cache-dir -r requirements.txt

# 指定容器啟動時執行的命令
CMD ["python", "main_fun.py"]
