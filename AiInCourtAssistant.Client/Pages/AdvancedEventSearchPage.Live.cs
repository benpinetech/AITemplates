#nullable enable
using Microsoft.AspNetCore.Components;
using Microsoft.JSInterop;
using System.Linq;
using System.Threading.Tasks;

namespace AiInCourtAssistant.Client.Pages
{
    // Live transcription glue – only depends on your existing fields:
    // _listening, _activeEventId, _searchResultList, LoginService
    public partial class AdvancedEventSearchPage : ComponentBase
    {
        [Inject] private NavigationManager NavLive { get; set; } = default!;
        [Inject] private IJSRuntime JsLive { get; set; } = default!;

        private string GetHubUrl()
            => NavLive.ToAbsoluteUri("/hubs/live-transcribe").ToString();

        // Called by your single Listen/Stop button
        private async Task ToggleListen()
        {
            var firstEventId = _searchResultList?.FirstOrDefault()?.EventID ?? 0;
            if (firstEventId == 0) return;

            var hubUrl = GetHubUrl();
            var token = LoginService.GetToken();

            await JsLive.InvokeVoidAsync("liveListen.toggle", new
            {
                hubUrl,
                accessToken = token,
                initialEventId = firstEventId,
                sampleRate = 48000
            });

            _listening = !_listening;
            _activeEventId = _listening ? firstEventId : 0;

            await InvokeAsync(StateHasChanged);
        }

        // Optional helpers (safe to keep)
        private async Task StartListenAsync()
        {
            if (_listening) return;

            var firstEventId = _searchResultList?.FirstOrDefault()?.EventID ?? 0;
            if (firstEventId == 0) return;

            var hubUrl = GetHubUrl();
            var token = LoginService.GetToken();

            await JsLive.InvokeVoidAsync("liveListen.start", new
            {
                hubUrl,
                accessToken = token,
                initialEventId = firstEventId,
                sampleRate = 48000
            });

            _listening = true;
            _activeEventId = firstEventId;
            await InvokeAsync(StateHasChanged);
        }

        private async Task StopListenAsync()
        {
            if (!_listening) return;

            try { await JsLive.InvokeVoidAsync("liveListen.stop"); }
            finally
            {
                _listening = false;
                _activeEventId = 0;
                await InvokeAsync(StateHasChanged);
            }
        }

        // If you ever call this, JS decides which row is active based on data-active="true"
        public async Task OnRowSwitchAsync(int eventId)
        {
            if (!_listening) return;
            _activeEventId = eventId;

            // JS function takes no args (it reads the active row)
            await JsLive.InvokeVoidAsync("liveListen.switchEvent");
        }
    }
}
