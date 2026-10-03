# Autonomous AI Agent Orchestration Platform

An autonomous, multi-agent software engineering orchestration engine built with Python, Redis, and Hermes 3. 

The platform implements an automated pipeline where specialized LLM agents collaborate to perform blast radius analysis, produce code inside sandboxed workspace environments, enforce local guardrails, and audit outputs for quality and security.

---

## 🏗 System Architecture

```text
                       ┌──────────────────────┐
                       │  User Task / Prompt  │
                       └──────────┬───────────┘
                                  │
                                  ▼
                       ┌──────────────────────┐
                       │   Project Ingestion  │
                       └──────────┬───────────┘
                                  │
                                  ▼
                       ┌──────────────────────┐
                       │   Impact Analyser    │
                       └──────────┬───────────┘
                                  │
                                  ▼
                       ┌──────────────────────┐
                       │      SWE Coder       │
                       └──────────┬───────────┘
                                  │
                                  ▼
                       ┌──────────────────────┐
                       │    Reviewer Agent    │
                       └──────────┬───────────┘
                                  │
                     ┌────────────┴────────────┐
                     ▼                         ▼
               [APPROVED]                 [REJECTED]
                    │                          │
                    ▼                          ▼
               Final Output           Self-Healing Loop / HITL