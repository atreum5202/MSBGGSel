export function registerAnthropicAdapter(ctx) {
  const { app, logger } = ctx;

  app.post('/v1/messages', async (req, reply) => {
      const { model, messages = [], system = '', tools = [], stream = false, max_tokens = 4096 } = req.body;
      
      let toolPrompt = '';
      if (tools && tools.length > 0) {
        toolPrompt = `\n\n=== TOOL INSTRUCTIONS ===\nYou have access to tools. You MUST use this exact XML format to call a tool:\n<tool_use>\n{"name": "tool_name", "input": {"arg_name": "arg_value"}}\n</tool_use>\nDo NOT wrap the XML in markdown blocks. Output only ONE tool_use at a time.\nTools available:\n${JSON.stringify(tools, null, 2)}`;
      }

      const openAiMessages = [];
      let combinedSystem = '';
      if (system) {
          if (Array.isArray(system)) {
              combinedSystem = system.map(s => s.text).join('\n');
          } else {
              combinedSystem = system;
          }
      }
      if (combinedSystem || toolPrompt) {
          openAiMessages.push({ role: 'system', content: combinedSystem + toolPrompt });
      }

      for (const msg of messages) {
          let content = msg.content;
          if (Array.isArray(msg.content)) {
              let textParts = [];
              for (const block of msg.content) {
                  if (block.type === 'text') textParts.push(block.text);
                  if (block.type === 'tool_result') {
                      let r = block.content;
                      if (Array.isArray(r)) r = r.map(c => (c.type === 'text' ? c.text : '')).join('\n');
                      textParts.push(`\n<tool_result id="${block.tool_use_id}">\n${r}\n</tool_result>\n`);
                  }
                  if (block.type === 'tool_use') {
                      textParts.push(`\n<tool_use>\n{"name": "${block.name}", "input": ${JSON.stringify(block.input)}}\n</tool_use>\n`);
                  }
              }
              content = textParts.join('\n');
          }
          openAiMessages.push({ role: msg.role === 'assistant' ? 'assistant' : 'user', content });
      }

      const payload = {
          model: model || 'claude-3-5-sonnet-20241022',
          messages: openAiMessages,
          stream: true, // Always stream from Clewd so we can parse large responses efficiently
          max_tokens
      };

      try {
          const clewdRes = await fetch('http://127.0.0.1:8444/v1/chat/completions', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload)
          });

          if (!clewdRes.ok) {
              const err = await clewdRes.text();
              reply.code(clewdRes.status).send({ error: { message: err } });
              return;
          }

          if (stream) {
              reply.raw.setHeader('Content-Type', 'text/event-stream');
              reply.raw.setHeader('Cache-Control', 'no-cache');
              reply.raw.setHeader('Connection', 'keep-alive');

              reply.raw.write(`event: message_start\ndata: ${JSON.stringify({ type: "message_start", message: { id: "msg_"+Date.now(), type: "message", role: "assistant", content: [], model, stop_reason: null, stop_sequence: null, usage: { input_tokens: 0, output_tokens: 0 } } })}\n\n`);

              let blockIndex = 0;
              reply.raw.write(`event: content_block_start\ndata: ${JSON.stringify({ type: "content_block_start", index: blockIndex, content_block: { type: "text", text: "" } })}\n\n`);

              const reader = clewdRes.body.getReader();
              const decoder = new TextDecoder();
              
              let buffer = '';
              let isParsingTool = false;
              let toolBuffer = '';
              let toolCount = 0;

              while (true) {
                  const { done, value } = await reader.read();
                  if (done) break;
                  const chunk = decoder.decode(value, { stream: true });
                  const lines = chunk.split('\n');
                  
                  for (const line of lines) {
                      if (line.startsWith('data: ') && line !== 'data: [DONE]') {
                          try {
                              const d = JSON.parse(line.slice(6));
                              const text = d.choices?.[0]?.delta?.content || '';
                              if (!text) continue;

                              if (!isParsingTool) {
                                  buffer += text;
                                  const toolStartIdx = buffer.indexOf('<tool_use>');
                                  if (toolStartIdx !== -1) {
                                      const textBefore = buffer.substring(0, toolStartIdx);
                                      if (textBefore) {
                                          reply.raw.write(`event: content_block_delta\ndata: ${JSON.stringify({ type: "content_block_delta", index: blockIndex, delta: { type: "text_delta", text: textBefore } })}\n\n`);
                                      }
                                      reply.raw.write(`event: content_block_stop\ndata: ${JSON.stringify({ type: "content_block_stop", index: blockIndex })}\n\n`);
                                      blockIndex++;
                                      
                                      isParsingTool = true;
                                      toolCount++;
                                      toolBuffer = buffer.substring(toolStartIdx + 10);
                                      buffer = '';
                                  } else {
                                      const lastLt = buffer.lastIndexOf('<');
                                      if (lastLt === -1 || buffer.length - lastLt > 10) {
                                          reply.raw.write(`event: content_block_delta\ndata: ${JSON.stringify({ type: "content_block_delta", index: blockIndex, delta: { type: "text_delta", text: buffer } })}\n\n`);
                                          buffer = '';
                                      } else if (!"<tool_use>".startsWith(buffer.substring(lastLt))) {
                                          reply.raw.write(`event: content_block_delta\ndata: ${JSON.stringify({ type: "content_block_delta", index: blockIndex, delta: { type: "text_delta", text: buffer } })}\n\n`);
                                          buffer = '';
                                      }
                                  }
                              } else {
                                  toolBuffer += text;
                                  const toolEndIdx = toolBuffer.indexOf('</tool_use>');
                                  if (toolEndIdx !== -1) {
                                      const jsonStr = toolBuffer.substring(0, toolEndIdx).trim();
                                      try {
                                          const tData = JSON.parse(jsonStr);
                                          reply.raw.write(`event: content_block_start\ndata: ${JSON.stringify({ type: "content_block_start", index: blockIndex, content_block: { type: "tool_use", id: "tool_"+Date.now(), name: tData.name, input: tData.input } })}\n\n`);
                                          
                                          // Anthropic expects input_json_delta
                                          reply.raw.write(`event: content_block_delta\ndata: ${JSON.stringify({ type: "content_block_delta", index: blockIndex, delta: { type: "input_json_delta", partial_json: JSON.stringify(tData.input) } })}\n\n`);
                                          
                                          reply.raw.write(`event: content_block_stop\ndata: ${JSON.stringify({ type: "content_block_stop", index: blockIndex })}\n\n`);
                                          blockIndex++;
                                      } catch (err) {
                                          logger.error({ err, jsonStr }, 'Failed to parse tool JSON');
                                      }
                                      isParsingTool = false;
                                      buffer = toolBuffer.substring(toolEndIdx + 11);
                                      toolBuffer = '';

                                      reply.raw.write(`event: content_block_start\ndata: ${JSON.stringify({ type: "content_block_start", index: blockIndex, content_block: { type: "text", text: "" } })}\n\n`);
                                  }
                              }
                          } catch(e) {}
                      }
                  }
              }

              if (buffer) {
                  reply.raw.write(`event: content_block_delta\ndata: ${JSON.stringify({ type: "content_block_delta", index: blockIndex, delta: { type: "text_delta", text: buffer } })}\n\n`);
              }
              reply.raw.write(`event: content_block_stop\ndata: ${JSON.stringify({ type: "content_block_stop", index: blockIndex })}\n\n`);

              const stopReason = toolCount > 0 ? 'tool_use' : 'end_turn';
              reply.raw.write(`event: message_delta\ndata: ${JSON.stringify({ type: "message_delta", delta: { stop_reason: stopReason, stop_sequence: null }, usage: { output_tokens: 10 } })}\n\n`);
              reply.raw.write(`event: message_stop\ndata: {"type": "message_stop"}\n\n`);
              reply.raw.end();
          } else {
              // NON-STREAMING mode
              let fullText = '';
              const reader = clewdRes.body.getReader();
              const decoder = new TextDecoder();
              while (true) {
                  const { done, value } = await reader.read();
                  if (done) break;
                  const chunk = decoder.decode(value, { stream: true });
                  const lines = chunk.split('\n');
                  for (const line of lines) {
                      if (line.startsWith('data: ') && line !== 'data: [DONE]') {
                          try {
                              const d = JSON.parse(line.slice(6));
                              fullText += d.choices?.[0]?.delta?.content || '';
                          } catch(e) {}
                      }
                  }
              }

              const blocks = [];
              let remainingText = fullText;
              let toolCount = 0;

              while (true) {
                  const s = remainingText.indexOf('<tool_use>');
                  const e = remainingText.indexOf('</tool_use>');
                  if (s !== -1 && e !== -1 && e > s) {
                      const before = remainingText.substring(0, s);
                      if (before) blocks.push({ type: 'text', text: before });
                      const jsonStr = remainingText.substring(s + 10, e).trim();
                      try {
                          const parsed = JSON.parse(jsonStr);
                          blocks.push({
                              type: 'tool_use',
                              id: 'tool_' + Date.now(),
                              name: parsed.name,
                              input: parsed.input
                          });
                          toolCount++;
                      } catch (err) {}
                      remainingText = remainingText.substring(e + 11);
                  } else {
                      if (remainingText) blocks.push({ type: 'text', text: remainingText });
                      break;
                  }
              }

              reply.send({
                  id: 'msg_' + Date.now(),
                  type: 'message',
                  role: 'assistant',
                  model: payload.model,
                  content: blocks,
                  stop_reason: toolCount > 0 ? 'tool_use' : 'end_turn',
                  usage: { input_tokens: 0, output_tokens: 10 }
              });
          }
      } catch (err) {
          logger.error({ err }, 'Clewd adapter error');
          reply.code(500).send({ error: { message: err.message } });
      }
  });
}
