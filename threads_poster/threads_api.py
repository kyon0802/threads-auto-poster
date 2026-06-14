"""
Threads API クライアント
公式エンドポイント (graph.threads.net) のみを使用。
- コンテナ作成 -> (メディアなら status をポーリング) -> 公開
- リプライ連結 (ツリー) は reply_to_id
- 長期トークンのリフレッシュ
"""
from __future__ import annotations

import time
import logging
import requests

logger = logging.getLogger("threads_api")

GRAPH_BASE = "https://graph.threads.net"
API_VERSION = "v1.0"


class ThreadsAPIError(Exception):
    pass


class ThreadsClient:
    def __init__(self, user_id: str, access_token: str, timeout: int = 30):
        self.user_id = str(user_id)
        self.access_token = access_token
        self.timeout = timeout

    # ---------- 投稿 ----------
    def create_container(
        self,
        text: str | None = None,
        media_type: str = "TEXT",
        image_url: str | None = None,
        video_url: str | None = None,
        reply_to_id: str | None = None,
        reply_control: str | None = None,
        children: list[str] | None = None,
        is_carousel_item: bool = False,
    ) -> str:
        """メディアコンテナを作成し creation_id を返す。

        CAROUSEL の場合は children（カルーセル各アイテムの creation_id）を渡す。
        is_carousel_item=True のときはカルーセルの子要素として作成する。
        """
        url = f"{GRAPH_BASE}/{API_VERSION}/{self.user_id}/threads"
        mt = media_type.upper()
        params: dict = {
            "media_type": mt,
            "access_token": self.access_token,
        }
        if text is not None:
            params["text"] = text
        if mt == "IMAGE" and image_url:
            params["image_url"] = image_url
        if mt == "VIDEO" and video_url:
            params["video_url"] = video_url
        if mt == "CAROUSEL" and children:
            params["children"] = ",".join(children)
        if is_carousel_item:
            params["is_carousel_item"] = "true"
        if reply_to_id:
            params["reply_to_id"] = str(reply_to_id)
        if reply_control:
            params["reply_control"] = reply_control  # everyone / accounts_you_follow / mentioned_only

        resp = requests.post(url, params=params, timeout=self.timeout)
        data = self._json(resp)
        if "id" not in data:
            raise ThreadsAPIError(f"container作成失敗: {data}")
        return data["id"]

    def create_carousel_item(
        self, image_url: str | None = None, video_url: str | None = None
    ) -> str:
        """カルーセルの子アイテム（画像 or 動画）のコンテナを作成し creation_id を返す。"""
        if image_url:
            return self.create_container(media_type="IMAGE", image_url=image_url, is_carousel_item=True)
        if video_url:
            return self.create_container(media_type="VIDEO", video_url=video_url, is_carousel_item=True)
        raise ThreadsAPIError("carousel item には image_url か video_url が必要です")

    def get_status(self, container_id: str) -> str:
        """コンテナの処理状態を返す: FINISHED / IN_PROGRESS / ERROR / EXPIRED / PUBLISHED"""
        url = f"{GRAPH_BASE}/{API_VERSION}/{container_id}"
        params = {"fields": "status,error_message", "access_token": self.access_token}
        resp = requests.get(url, params=params, timeout=self.timeout)
        data = self._json(resp)
        return data.get("status", "UNKNOWN")

    def wait_until_ready(self, container_id: str, max_wait: int = 120, interval: int = 5) -> None:
        """メディアの処理完了を待つ。テキストのみなら即 FINISHED のことが多い。"""
        waited = 0
        while waited < max_wait:
            status = self.get_status(container_id)
            if status in ("FINISHED", "PUBLISHED"):
                return
            if status in ("ERROR", "EXPIRED"):
                raise ThreadsAPIError(f"container処理失敗 status={status}")
            time.sleep(interval)
            waited += interval
        raise ThreadsAPIError(f"container処理がタイムアウト ({max_wait}s)")

    def publish(self, creation_id: str) -> str:
        """コンテナを公開し、公開後の投稿IDを返す。"""
        url = f"{GRAPH_BASE}/{API_VERSION}/{self.user_id}/threads_publish"
        params = {"creation_id": creation_id, "access_token": self.access_token}
        resp = requests.post(url, params=params, timeout=self.timeout)
        data = self._json(resp)
        if "id" not in data:
            raise ThreadsAPIError(f"公開失敗: {data}")
        return data["id"]

    def post(
        self,
        text: str | None = None,
        media_type: str = "TEXT",
        image_url: str | None = None,
        video_url: str | None = None,
        media_urls: list[str] | None = None,
        reply_to_id: str | None = None,
        reply_control: str | None = None,
        poll_media: bool = True,
    ) -> str:
        """create -> (wait) -> publish をまとめて実行し、公開後IDを返す。

        CAROUSEL は media_urls（2件以上）を渡す。各アイテムを子コンテナ化 →
        親カルーセルコンテナ作成 → 公開、の順で逐次処理する。
        """
        mt = media_type.upper()
        if mt == "CAROUSEL":
            urls = [u for u in (media_urls or []) if u]
            if len(urls) < 2:
                raise ThreadsAPIError("CAROUSEL には2件以上のメディアURLが必要です")
            children: list[str] = []
            for u in urls:
                if self._looks_like_video(u):
                    cid = self.create_carousel_item(video_url=u)
                else:
                    cid = self.create_carousel_item(image_url=u)
                if poll_media:
                    self.wait_until_ready(cid)
                children.append(cid)
            carousel_id = self.create_container(
                text=text,
                media_type="CAROUSEL",
                children=children,
                reply_to_id=reply_to_id,
                reply_control=reply_control,
            )
            if poll_media:
                self.wait_until_ready(carousel_id)
            return self.publish(carousel_id)

        creation_id = self.create_container(
            text=text,
            media_type=media_type,
            image_url=image_url,
            video_url=video_url,
            reply_to_id=reply_to_id,
            reply_control=reply_control,
        )
        # メディアは処理完了を待ってから公開（公式推奨）。テキストは基本不要だが安全側で確認。
        if mt in ("IMAGE", "VIDEO") and poll_media:
            self.wait_until_ready(creation_id)
        else:
            time.sleep(2)  # テキストでも直後公開は失敗することがあるため軽く待つ
        return self.publish(creation_id)

    @staticmethod
    def _looks_like_video(url: str) -> bool:
        ext = url.lower().split("?")[0].rsplit(".", 1)[-1]
        return ext in ("mp4", "mov", "m4v")

    # ---------- トークン ----------
    @staticmethod
    def refresh_long_lived_token(access_token: str, timeout: int = 30) -> dict:
        """
        長期トークン(60日)をリフレッシュ。トークンは24時間以上経過かつ未失効である必要がある。
        返り値: {"access_token": ..., "expires_in": 秒}
        """
        url = f"{GRAPH_BASE}/refresh_access_token"
        params = {"grant_type": "th_refresh_token", "access_token": access_token}
        resp = requests.get(url, params=params, timeout=timeout)
        data = resp.json()
        if "access_token" not in data:
            raise ThreadsAPIError(f"トークンリフレッシュ失敗: {data}")
        return data

    @staticmethod
    def exchange_for_long_lived(short_lived_token: str, client_secret: str, timeout: int = 30) -> dict:
        """短期トークン(1h)を長期トークン(60日)に交換。初回ブートストラップ用。"""
        url = f"{GRAPH_BASE}/access_token"
        params = {
            "grant_type": "th_exchange_token",
            "client_secret": client_secret,
            "access_token": short_lived_token,
        }
        resp = requests.get(url, params=params, timeout=timeout)
        data = resp.json()
        if "access_token" not in data:
            raise ThreadsAPIError(f"長期トークン交換失敗: {data}")
        return data

    # ---------- 内部 ----------
    def _json(self, resp: requests.Response) -> dict:
        try:
            data = resp.json()
        except Exception:
            raise ThreadsAPIError(f"非JSON応答 status={resp.status_code} body={resp.text[:300]}")
        if resp.status_code >= 400:
            raise ThreadsAPIError(f"APIエラー status={resp.status_code} body={data}")
        return data
