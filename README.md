


當 Docker 容器啟動後，你可以使用以下幾種方式來即時查看輸出，而不是等到報錯才看到內容：

1. docker build -t my_solana_bot .
2. docker run -d --name solana_bot my_solana_bot
3. docker logs -f solana_bot

🛠 指令解析
docker run -d --name solana_bot my_solana_bot
docker run：用來啟動一個新的 Docker 容器。
-d：讓容器在 背景（detached mode） 運行，而不是前台佔用終端機。
--name solana_bot：指定 容器名稱 為 solana_bot，這樣可以方便管理和識別容器。
my_solana_bot：指定要運行的 Docker 映像（image），這是你先前用 docker build -t my_solana_bot . 建立的映像。