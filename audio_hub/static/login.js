(() => {
  const savedTheme = localStorage.getItem("audio-hub-theme");
  if (savedTheme) document.documentElement.dataset.theme = savedTheme;

  const form = document.getElementById("login-form");
  const button = document.getElementById("login-button");
  const errorBox = document.getElementById("login-error");
  const password = document.getElementById("password");
  const csrf = document.querySelector('meta[name="csrf-token"]').content;

  document.getElementById("toggle-password").addEventListener("click", () => {
    const visible = password.type === "text";
    password.type = visible ? "password" : "text";
    document.getElementById("toggle-password").setAttribute(
      "aria-label",
      visible ? "显示密码" : "隐藏密码",
    );
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.hidden = true;
    button.disabled = true;
    button.querySelector("span").textContent = "正在验证…";
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrf,
        },
        body: JSON.stringify({
          username: document.getElementById("username").value.trim(),
          password: password.value,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "登录失败");
      window.location.replace("/");
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.hidden = false;
      password.select();
    } finally {
      button.disabled = false;
      button.querySelector("span").textContent = "登录控制台";
    }
  });
})();
