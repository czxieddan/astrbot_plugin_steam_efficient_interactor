from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image, Plain

from .runtime_utils import create_temp_png, safe_remove


class SteamNotificationService:
    def __init__(self, owner):
        self.owner = owner

    async def send_notification(self, group_id: str, text: str | None = None, image_bytes: bytes | None = None) -> bool:
        notify_session = self.owner.notify_sessions.get(group_id)
        if not notify_session:
            return False
        chain_items = []
        temp_path = None
        if text:
            chain_items.append(Plain(text))
        if image_bytes:
            temp_path = create_temp_png(image_bytes)
            chain_items.append(Image.fromFileSystem(temp_path))
        try:
            await self.owner.context.send_message(notify_session, MessageChain(chain_items))
            return True
        except Exception as error:
            self.owner.log_throttle.log(f"send_message:{group_id}", logger.warning, f"发送通知失败: {error}")
            return False
        finally:
            safe_remove(temp_path)

    async def send_image_to_event(self, event, image_bytes: bytes | None) -> bool:
        if not image_bytes:
            return False
        temp_path = create_temp_png(image_bytes)
        try:
            await event.send(event.image_result(temp_path))
            return True
        except Exception as error:
            self.owner.log_throttle.log("tool_image_send", logger.warning, f"工具图片发送失败: {error}")
            return False
        finally:
            safe_remove(temp_path)

    async def summarize_tool_result_with_llm(self, tool_name: str, raw_payload: str, *, image_sent: bool = False) -> str:
        return raw_payload

    async def send_llm_wrapped_notification(
        self,
        group_id: str,
        tool_name: str,
        raw_payload: str,
        image_bytes: bytes | None = None,
    ) -> bool:
        if image_bytes:
            return await self.send_notification(group_id, image_bytes=image_bytes)
        summary = await self.summarize_tool_result_with_llm(tool_name, raw_payload, image_sent=False)
        return await self.send_notification(group_id, text=summary)