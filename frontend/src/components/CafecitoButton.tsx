const CAFECITO_USERNAME = "nicotti";

export function CafecitoButton() {
  return (
    <a
      href={`https://cafecito.app/${CAFECITO_USERNAME}`}
      rel="noopener noreferrer"
      target="_blank"
      className="inline-flex items-center"
    >
      <img
        srcSet="https://cdn.cafecito.app/imgs/buttons/button_1.png 1x, https://cdn.cafecito.app/imgs/buttons/button_1_2x.png 2x, https://cdn.cafecito.app/imgs/buttons/button_1_3.75x.png 3.75x"
        src="https://cdn.cafecito.app/imgs/buttons/button_1.png"
        alt="Invitame un café en cafecito.app"
        className="h-8 w-auto"
      />
    </a>
  );
}
