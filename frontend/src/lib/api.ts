export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
export const agUiUrl = () => `${API_URL}/agui`;
export const modelsUrl = () => `${API_URL}/models`;
