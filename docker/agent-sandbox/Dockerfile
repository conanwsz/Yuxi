FROM enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest

COPY fonts/NotoSansCJKsc-Regular.otf /usr/local/share/fonts/yuxi/
COPY fonts/LICENSE /usr/local/share/licenses/noto-sans-cjk-sc/LICENSE
COPY matplotlibrc /opt/yuxi/matplotlibrc

RUN fc-cache -f

ENV MATPLOTLIBRC=/opt/yuxi/matplotlibrc
