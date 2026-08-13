# -*- coding: utf-8 -*-
"""
parser/event_logger.py
======================
Единый журнал событий для GGselV7.

Структура записи:
  - time: время события (ISO 8601)
  - entity: сущность (product, deal, offer, system, etc.)
  - stage: этап жизненного цикла
  - level: уровень (info, warning, error, critical)
  - reason_code: код причины/ошибки
  - message: понятное сообщение
  - technical_detail: техническая деталь
  - action: возможное действие
"""
from __future__ import annotations
import logging
import sqlite3
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum

from .db_init import get_db_path


class LogLevel(Enum):
    """Уровни логирования."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Entity(Enum):
    """Типы сущностей."""
    PRODUCT = "product"
    DEAL = "deal"
    OFFER = "offer"
    SYSTEM = "system"
    PARSER = "parser"
    ECONOMICS = "economics"
    AI = "ai"
    USER = "user"


class Stage(Enum):
    """Этапы жизненного цикла."""
    PARSED = "parsed"
    ECONOMICS_CHECKED = "economics_checked"
    AI_RECOMMENDED = "ai_recommended"
    APPROVED_BY_OWNER = "approved_by_owner"
    DRAFT_CREATED = "draft_created"
    PUBLISHED = "published"
    SOLD = "sold"
    SOURCED = "sourced"
    DELIVERED = "delivered"
    CLOSED = "closed"


class EventLogger:
    """Единый журнал событий."""
    
    def __init__(self):
        self.logger = logging.getLogger("ggselv7.event_logger")
    
    def log(
        self,
        entity: str,
        entity_id: str,
        stage: str,
        level: str = "info",
        reason_code: Optional[str] = None,
        message: str = "",
        technical_detail: Optional[str] = None,
        action: Optional[str] = None,
    ) -> bool:
        """
        Записывает событие в журнал.
        
        Args:
            entity: тип сущности (product, deal, offer, system, etc.)
            entity_id: идентификатор сущности
            stage: этап жизненного цикла
            level: уровень (info, warning, error, critical)
            reason_code: код причины/ошибки
            message: понятное сообщение
            technical_detail: техническая деталь
            action: возможное действие
        
        Returns:
            True если запись успешна, иначе False
        """
        try:
            conn = sqlite3.connect(get_db_path(), timeout=10.0)
            try:
                conn.execute(
                    """
                    INSERT INTO event_log (
                        time, entity, entity_id, stage, level, 
                        reason_code, message, technical_detail, action
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        datetime.utcnow().isoformat(),
                        entity,
                        entity_id,
                        stage,
                        level,
                        reason_code or "",
                        message,
                        technical_detail or "",
                        action or "",
                    ),
                )
                conn.commit()
                return True
            except Exception as e:
                self.logger.error("Failed to insert event log: %s", e)
                conn.rollback()
                return False
            finally:
                conn.close()
        except Exception as e:
            self.logger.error("Failed to connect to DB for event log: %s", e)
            return False
    
    def log_product_event(
        self,
        product_id: str,
        stage: str,
        level: str = "info",
        reason_code: Optional[str] = None,
        message: str = "",
        technical_detail: Optional[str] = None,
        action: Optional[str] = None,
    ) -> bool:
        """Удобная функция для логирования событий товара."""
        return self.log(
            entity="product",
            entity_id=product_id,
            stage=stage,
            level=level,
            reason_code=reason_code,
            message=message,
            technical_detail=technical_detail,
            action=action,
        )
    
    def log_deal_event(
        self,
        deal_id: str,
        stage: str,
        level: str = "info",
        reason_code: Optional[str] = None,
        message: str = "",
        technical_detail: Optional[str] = None,
        action: Optional[str] = None,
    ) -> bool:
        """Удобная функция для логирования событий сделки."""
        return self.log(
            entity="deal",
            entity_id=deal_id,
            stage=stage,
            level=level,
            reason_code=reason_code,
            message=message,
            technical_detail=technical_detail,
            action=action,
        )
    
    def log_system_event(
        self,
        stage: str,
        level: str = "info",
        reason_code: Optional[str] = None,
        message: str = "",
        technical_detail: Optional[str] = None,
        action: Optional[str] = None,
    ) -> bool:
        """Удобная функция для логирования системных событий."""
        return self.log(
            entity="system",
            entity_id="system",
            stage=stage,
            level=level,
            reason_code=reason_code,
            message=message,
            technical_detail=technical_detail,
            action=action,
        )
    
    def get_events(
        self,
        entity: Optional[str] = None,
        entity_id: Optional[str] = None,
        stage: Optional[str] = None,
        level: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Dict[str, Any]]:
        """
        Получает события из журнала с фильтрацией.
        
        Args:
            entity: фильтр по типу сущности
            entity_id: фильтр по ID сущности
            stage: фильтр по этапу
            level: фильтр по уровню
            limit: лимит записей
            offset: смещение
        
        Returns:
            Список словарей с событиями
        """
        try:
            conn = sqlite3.connect(get_db_path(), timeout=10.0)
            conn.row_factory = sqlite3.Row
            try:
                query = "SELECT * FROM event_log WHERE 1=1"
                params = []
                
                if entity:
                    query += " AND entity = ?"
                    params.append(entity)
                
                if entity_id:
                    query += " AND entity_id = ?"
                    params.append(entity_id)
                
                if stage:
                    query += " AND stage = ?"
                    params.append(stage)
                
                if level:
                    query += " AND level = ?"
                    params.append(level)
                
                query += " ORDER BY time DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])
                
                rows = conn.execute(query, params).fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()
        except Exception as e:
            self.logger.error("Failed to get events: %s", e)
            return []
    
    def get_entity_history(self, entity: str, entity_id: str) -> list[Dict[str, Any]]:
        """
        Получает полную историю сущности.
        
        Args:
            entity: тип сущности
            entity_id: идентификатор сущности
        
        Returns:
            Список событий хронологически
        """
        return self.get_events(entity=entity, entity_id=entity_id, limit=1000)


# Глобальный экземпляр логгера
_default_logger: Optional[EventLogger] = None


def get_event_logger() -> EventLogger:
    """Возвращает глобальный экземпляр логгера событий."""
    global _default_logger
    if _default_logger is None:
        _default_logger = EventLogger()
    return _default_logger


def log_event(
    entity: str,
    entity_id: str,
    stage: str,
    level: str = "info",
    reason_code: Optional[str] = None,
    message: str = "",
    technical_detail: Optional[str] = None,
    action: Optional[str] = None,
) -> bool:
    """Удобная функция для логирования событий."""
    return get_event_logger().log(
        entity=entity,
        entity_id=entity_id,
        stage=stage,
        level=level,
        reason_code=reason_code,
        message=message,
        technical_detail=technical_detail,
        action=action,
    )