using System.Text.Json.Serialization;

namespace AiInCourtAssistant.Shared.Models
{
    public class LoginDto
    {
        [JsonPropertyName("Login")]
        public string Username { get; set; } = string.Empty;

        public string Password { get; set; } = string.Empty;
    }

    public class LoginResult
    {
        public string Token { get; set; } = string.Empty;
    }
}
