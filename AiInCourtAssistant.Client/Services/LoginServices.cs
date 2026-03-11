using System.Net.Http.Headers;
using System.Net.Http.Json;
using AiInCourtAssistant.Shared.Models;
using Microsoft.JSInterop;

namespace AiInCourtAssistant.Client.Services
{
    public class LoginService
    {
        private readonly HttpClient _http;
        private readonly IJSRuntime _jsRuntime;
        private string? _token;

        public LoginService(HttpClient http, IJSRuntime jsRuntime)
        {
            _http = http;
            _jsRuntime = jsRuntime;
        }

        public async Task<bool> LoginAsync(LoginDto credentials)
        {
            try
            {
                Console.WriteLine("[LoginService] Sending login request...");
                var response = await _http.PostAsJsonAsync("api/auth/login", credentials);
                Console.WriteLine($"[LoginService] Response status: {(int)response.StatusCode}");

                if (!response.IsSuccessStatusCode)
                {
                    Console.WriteLine("[LoginService] Login failed.");
                    return false;
                }

                var result = await response.Content.ReadFromJsonAsync<LoginResult>();
                if (result == null || string.IsNullOrEmpty(result.Token))
                {
                    Console.WriteLine("[LoginService] Token is null or empty.");
                    return false;
                }

                _token = result.Token;
                Console.WriteLine($"[LoginService] Token received and stored.");

                // ✅ Save to localStorage
                await _jsRuntime.InvokeVoidAsync("localStorage.setItem", "authToken", _token);

                return true;
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[LoginService] EXCEPTION: {ex.Message}");
                return false;
            }
        }

        public string? GetToken() => _token;

        public async Task<string?> GetTokenAsync()
        {
            if (!string.IsNullOrWhiteSpace(_token))
                return _token;

            // ✅ Load from localStorage if not cached in memory
            _token = await _jsRuntime.InvokeAsync<string>("localStorage.getItem", "authToken");
            return _token;
        }

        public async Task LogoutAsync()
        {
            _token = null;
            await _jsRuntime.InvokeVoidAsync("localStorage.removeItem", "authToken");
        }
    }
}
