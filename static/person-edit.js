function editPerson(personId) {
  const person = peopleCache.find((item) => item.id === personId);
  if (!person) return;
  openModal(
    `Edit student · ${person.name}`,
    `<label>Person identifier<input name="person_code" required value="${esc(person.code)}"></label>
     <div class="two-col">
       <label>First name<input name="first_name" required value="${esc(person.first_name)}"></label>
       <label>Last name<input name="last_name" required value="${esc(person.last_name)}"></label>
     </div>
     <label>Email<input name="email" type="email" value="${esc(person.email || '')}"></label>
     <label>Department (optional)<input name="department" value="${esc(person.department || '')}" placeholder="e.g. Computer Science"></label>
     <label>Class / group<input name="group_name" required value="${esc(person.group || '')}"></label>
     <small class="muted class-group-hint">Match this value to the student's class session. Changing the group does not remove the saved face template.</small>
     <label>Attendance mode<select name="mode">
       <option value="education" ${person.mode === 'education' ? 'selected' : ''}>Education</option>
       <option value="workplace" ${person.mode === 'workplace' ? 'selected' : ''}>Workplace</option>
     </select></label>`,
    async (form) => {
      const values = Object.fromEntries(form);
      await api(`/api/people/${personId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
    },
    'Save profile',
  );
}

function addProfileEditButtons() {
  if (!['super_admin', 'org_admin', 'academic_admin'].includes(appRole)) return;
  const rows = [...document.querySelectorAll('#peopleRows tr')];
  rows.forEach((row, index) => {
    const person = peopleCache[index];
    const actions = row.cells[5];
    if (!person || !actions || actions.querySelector('[data-edit-person]')) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'text-btn';
    button.dataset.editPerson = String(person.id);
    button.textContent = 'Edit profile';
    actions.prepend(button);
  });
}

document.querySelector('#peopleRows').addEventListener('click', (event) => {
  const button = event.target.closest('[data-edit-person]');
  if (button) editPerson(Number(button.dataset.editPerson));
});

new MutationObserver(addProfileEditButtons).observe(document.querySelector('#peopleRows'), { childList: true });

new MutationObserver(() => {
  const input = document.querySelector('#modalBody input[name="group_name"]');
  if (!input) return;
  const label = input.closest('label');
  if (!label) return;
  if (label.firstChild?.nodeType === Node.TEXT_NODE) label.firstChild.textContent = 'Class / group';
  input.required = true;
  if (!input.placeholder) input.placeholder = 'e.g. Fourth Year / Form 3A';
  if (!document.querySelector('#modalBody .class-group-hint')) {
    const hint = document.createElement('small');
    hint.className = 'muted class-group-hint';
    hint.textContent = 'Use the same class/group value on the student profile and attendance session.';
    label.append(hint);
  }
}).observe(document.querySelector('#modalBody'), { childList: true, subtree: true });
