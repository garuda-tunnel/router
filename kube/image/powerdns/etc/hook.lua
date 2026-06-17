-- postresolve runs after the packet has been answered, and can be used to change things
-- or still drop
local websocket = require "http.websocket"
local cjson = require("cjson")
local ws_uri = "ws://127.0.0.1:8765/"
local ws = websocket.new_from_uri(ws_uri)
ws:connect()
pdnslog("websocket uri" .. ws_uri, pdns.loglevels.Debug)
function postresolve(dq)
    pdnslog("postresolve called for " .. dq.qname:toString())
    local records = dq:getRecords()
    local modified = false
    for k, v in pairs(records) do
        pdnslog(k .. " " .. v.name:toString() .. " " .. v:getContent() .. " " .. v.type, pdns.loglevels.Debug)
        local message = {
            query = dq.qname:toString(),
            name = v.name:toString(),
            type = v.type,
            content = v:getContent(),
            ttl = v.ttl
        }
        --        if string.len(message) > 0 then
        local json_message = cjson.encode(message)
        pdnslog(json_message, pdns.loglevels.Debug)
        if v.type == pdns.A then
            modified = true
            local success, response
            if not ws:send(json_message) then
                pdnslog('Reconnecting ipt-server')
                ws = nil
                ws = websocket.new_from_uri(ws_uri)
                ws:connect()
                success = ws:send(json_message)
                if not success then
                    pdnslog('Failed to send message after reconnection', pdns.loglevels.Error)
                end
            end

            response = ws:receive(0.25)
            if response then
                pdnslog("Received response: " .. response, pdns.loglevels.Debug)
                local success, response_data = pcall(cjson.decode, response)
                if success and response_data and response_data.ttl then
                    v.ttl = response_data.ttl
                    pdnslog("Setting TTL to " .. response_data.ttl, pdns.loglevels.Debug)
                else
                    v.ttl = 1
                    pdnslog("Using degraded TTL (1) due to invalid response", pdns.loglevels.Warning)
                end
            else
                v.ttl = 1
                pdnslog("Using degraded TTL (1) due to response timeout", pdns.loglevels.Warning)
            end
        end
    end
    if modified then
        dq:setRecords(records)
        pdnslog("TTL patched for records", pdns.loglevels.Debug)
    end
    return true
end

function maintenance()
    -- to handle keepalive ping/pong
    local x = ws:receive(0)
    if x ~= nil then
        pdnslog(x)
    end
end
