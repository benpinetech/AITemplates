using AiInCourtAssistant.Server.Services;
using AiInCourtAssistant.Shared.Models;
using Microsoft.AspNetCore.Mvc;

namespace AiInCourtAssistant.Server.Controllers
{
    [ApiController]
    [Route("api/event")]
    public class EventController : ControllerBase
    {
        private readonly IEventService _eventService;

        public EventController(IEventService eventService)
        {
            _eventService = eventService;
        }

        // Local update endpoint (used for mock updates or testing)
        [HttpPut("{id}")]
        public async Task<IActionResult> UpdateEvent(int id, [FromBody] EventUpdateDto update)
        {
            Console.WriteLine($"[Local API] PUT /api/event/{id} HIT");

            if (update == null)
            {
                Console.WriteLine("❌ UpdateEventDto is null.");
                return BadRequest("No update payload provided.");
            }

            Console.WriteLine($"Updating EventID: {update.EventID}, New Date: {update.StartDate}");

            var token = Request.Headers["Authorization"].ToString()?.Replace("Bearer ", "");
            if (string.IsNullOrWhiteSpace(token))
                return Unauthorized("Missing bearer token.");

            var success = await _eventService.UpdateEventAsync(update);

            return success ? NoContent() : NotFound();
        }

    }
}
