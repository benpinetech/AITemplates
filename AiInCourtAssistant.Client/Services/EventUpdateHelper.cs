using AiInCourtAssistant.Shared.Models;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;

namespace AiInCourtAssistant.Client.Services
{
    public static class EventUpdateHelper
    {
        /// <summary>
        /// Fetch full event via proxy, apply inline edits, then POST the full payload back through the proxy.
        /// </summary>
        public static async Task SaveInlineEdit(EditableEvent ev, HttpClient http, LoginService loginService)
        {
            var token = loginService.GetToken();
            if (string.IsNullOrWhiteSpace(token))
            {
                Console.WriteLine("🚫 No token found.");
                return;
            }

            try
            {
                // 1) Get the full event from proxy so required fields are populated
                var getReq = new HttpRequestMessage(HttpMethod.Get, $"api/proxy/event/{ev.EventID}");
                getReq.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);

                var getRes = await http.SendAsync(getReq);
                if (!getRes.IsSuccessStatusCode)
                {
                    Console.WriteLine($"❌ Failed to retrieve event ID {ev.EventID}: {getRes.ReasonPhrase}");
                    return;
                }

                var rawJson = await getRes.Content.ReadAsStringAsync();
                var options = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };

                var original = JsonSerializer.Deserialize<FullEventDto>(rawJson, options);
                if (original is null)
                {
                    Console.WriteLine($"❌ Deserialized object is null for event ID {ev.EventID}");
                    return;
                }

                // 2) Apply updates while preserving existing values if not changed
                original.EventID = ev.EventID;

                if (!string.IsNullOrWhiteSpace(ev.UpdatedNotes))
                    original.Note = ev.UpdatedNotes;

                if (!string.IsNullOrWhiteSpace(ev.UpdatedStatus))
                    original.Status = ev.UpdatedStatus;

                if (!string.IsNullOrWhiteSpace(ev.UpdatedType))
                    original.Type = ev.UpdatedType;

                if (string.IsNullOrWhiteSpace(original.Type) && !string.IsNullOrWhiteSpace(ev.EventType))
                    original.Type = ev.EventType;

                Console.WriteLine($"🚀 EVENT {ev.EventID} → Final StartDate: {original.StartDate}, Final Type: {original.Type}");
                Console.WriteLine("📦 Final Payload: " + JsonSerializer.Serialize(original));
                Console.WriteLine($"📦 Final StartDate before POST: {original.StartDate?.ToString("yyyy-MM-ddTHH:mm:ss") ?? "NULL"}");

                // 3) Send update
                var postRes = await http.PostAsJsonAsync("api/proxy/event/update", original);
                var postContent = await postRes.Content.ReadAsStringAsync();

                if (postRes.IsSuccessStatusCode)
                {
                    Console.WriteLine($"✅ Event {ev.EventID} updated.");

                    // ✅ Sync UI from final payload
                    ev.Notes = original.Note;
                    ev.EventStatus = original.Status;
                    ev.StartDate = original.StartDate;
                    ev.EventType = original.Type;
                    ev.IsEditing = false;

                    ev.UpdatedNotes = ev.Notes;
                    ev.UpdatedStatus = ev.EventStatus;
                    ev.UpdatedType = ev.EventType;
                }
                else
                {
                    Console.WriteLine($"❌ Update failed: {postRes.StatusCode} - {postContent}");
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine("🔥 EXCEPTION in SaveInlineEdit: " + ex.Message);
            }
        }
    }
}
