using PineCone.BuildingBlocks.Data.Constants;

namespace DocumentGenerator.Exceptions
{
    public class PineTemplateGeneratorException: System.Exception
    {
        int errorCode = -1;
        public PineTemplateGeneratorException() : base()
        {
        }

        // Constructor that takes a message
        public PineTemplateGeneratorException(string message) : base(message)
        {
        }

        public PineTemplateGeneratorException(int errorCode) : base(GetErrorMessage(errorCode))
        {
            this.errorCode = errorCode;
        }

        public PineTemplateGeneratorException(int errorCode, string data) : base(GetErrorMessage(errorCode, data))
        {
            this.errorCode = errorCode;
        }

        public PineTemplateGeneratorException(string message, int errorCode) : base(message)
        {
            this.errorCode = errorCode;
        }

        // Constructor that takes a message and an inner exception
        public PineTemplateGeneratorException(string message, Exception innerException) : base(message, innerException)
        {
        }

        // Constructor for serialization (used for remoting and serialization)
        protected PineTemplateGeneratorException(System.Runtime.Serialization.SerializationInfo info, System.Runtime.Serialization.StreamingContext context) : base(info, context)
        {
        }

        private static string GetErrorMessage(int errorCode, string? data = null)
        {
            var message = $"ERROR CODE {errorCode} | " +
                DocumentGeneratorErrorCodes.GetErrorCodeMessage(errorCode);

            if (data is not null)
            {
                message += $"| Error Data {data}";
            }

            return message;
        }
    }
}
