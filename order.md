docker build --platform linux/amd64 --provenance=false -f baoqi/Dockerfile -t swr.cn-south-1.myhuaweicloud.com/xintong/ai-baoqi-dt:1.0.0 .

docker login -u cn-south-1@HPUACNL9IKNH6XBLM1DX -p 4f5403627fef9b60e526eaec95161138db90a9929351dd650b7b9b9ae4c70dd8 swr.cn-south-1.myhuaweicloud.com  登录华为云

docker push swr.cn-south-1.myhuaweicloud.com/xintong/ai-baoqi-dt:1.0.0

docker pull swr.cn-south-1.myhuaweicloud.com/xintong/ai-baoqi-dt:1.0.0

docker compose up -d

docker compose down

docker compose stop

docker compose restart

docker logs **-f** baoqi-c
