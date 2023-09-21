
import React, { useState, useEffect } from "react";
import { Icon } from "@iconify/react";
import "./users.scss";

const Users = () => {
  const [usersList, setUsersList] = useState([]);
  const [newUser, setNewUser] = useState({
    name: "",
    age: "",
    dob: "",
    gender: "",
    food: "",
    hobbies: "",
  });
  const [showAddUserModal, setShowAddUserModal] = useState(false);
  const [selectedUser, setSelectedUser] = useState(null); // To track the user being viewed
  const [currentPage, setCurrentPage] = useState(1);
  const usersPerPage = 6;

  // user data from localStorage
  useEffect(() => {
    const storedUsers = JSON.parse(localStorage.getItem("users")) || [];
    setUsersList(storedUsers);
  }, []);

  // Save user data to localStorage
  useEffect(() => {
    localStorage.setItem("users", JSON.stringify(usersList));
  }, [usersList]);

  const handleAddUser = () => {
    setShowAddUserModal(true);
    setSelectedUser(null); 
  };

  const handleSaveUser = () => {
    if (newUser.name && newUser.age) {
      if (selectedUser === null) {
        // Adding a new user
        setUsersList([...usersList, newUser]);
      } else {
        // Editing an existing user
        const updatedUsers = [...usersList];
        updatedUsers[selectedUser] = newUser;
        setUsersList(updatedUsers);
        setSelectedUser(null); 
      }
      setNewUser({
        name: "",
        age: "",
        dob: "",
        gender: "",
        food: "",
        hobbies: "",
      });
      setShowAddUserModal(false);
    }
  };

  const handleDeleteUser = (index) => {
    const updatedUsers = [...usersList];
    updatedUsers.splice(index, 1);
    setUsersList(updatedUsers);
  };

  const handleEditUser = (index) => {
    const userToEdit = usersList[index];
    setNewUser({ ...userToEdit });
    setSelectedUser(index);
    setShowAddUserModal(true);
  };

  const handleViewUser = (index) => {
    setSelectedUser(index);
  };

  const indexOfLastUser = currentPage * usersPerPage;
  const indexOfFirstUser = indexOfLastUser - usersPerPage;
  const currentUsers = usersList.slice(indexOfFirstUser, indexOfLastUser);

  const pageNumbers = Array(Math.ceil(usersList.length / usersPerPage))
    .fill()
    .map((_, i) => i + 1);

  const renderPageNumbers = pageNumbers.map((number) => (
    <li
      key={number}
      className={`page-number ${number === currentPage ? "active" : ""}`}
      onClick={() => setCurrentPage(number)}
    >
      {number}
    </li>
  ));

  return (
    <>
      <div className="top">
        <div className="title">LIST OF USERS</div>
        <div className="btn">
          <button className="btn" onClick={handleAddUser}>
            ADD USER
          </button>
        </div>
      </div>

      <div className="cards">
        {currentUsers.map((user, index) => (
          <div className="card" key={index}>
            <div className="head">
              <h2>{user.name}</h2>
              <div className="avail">
                <Icon
                  icon="carbon:checkmark-filled"
                  width={"24px"}
                  height={"24px"}
                  color="green"
                />
              </div>
            </div>
            <div className="body">
              <div className="ques">AGE:</div>
              <div className="ans">{user.age}</div>
            </div>
            <div className="body">
              <div className="ques">DOB:</div>
              <div className="ans">{user.dob}</div>
            </div>
            <div className="body">
              <div className="ques">GENDER:</div>
              <div className="ans">{user.gender}</div>
            </div>
            <div className="body">
              <div className="ques">FOOD:</div>
              <div className="ans">{user.food}</div>
            </div>
            <div className="body">
              <div className="ques">HOBBIES:</div>
              <div className="ans">{user.hobbies}</div>
            </div>
            <div className="foot">
              <button
                className="btn2"
                onClick={() => handleDeleteUser(index)}
              >
                DELETE
              </button>
              <button className="btn1" onClick={() => handleEditUser(index)}>
                EDIT
              </button>
              <button
                className="btn3"
                onClick={() => handleViewUser(index)}
              >
                VIEW
              </button>
            </div>
          </div>
        ))}
      </div>

      {showAddUserModal && (
        <div className="modal">
          <div className="modal-content">
            <h2>{selectedUser !== null ? "Edit User" : "Add User"}</h2>
            <div className="modal-detail">
            <label>Name:</label>
            <input
              type="text"
              value={newUser.name}
              onChange={(e) =>
                setNewUser({ ...newUser, name: e.target.value })
              }
            />
            </div>
            <div className="modal-detail">
            <label>Age:</label>
            <input
              type="text"
              value={newUser.age}
              onChange={(e) =>
                setNewUser({ ...newUser, age: e.target.value })
              }
            />
            </div>
            <div className="modal-detail">
            <label>DOB:</label>
            <input
              type="text"
              value={newUser.dob}
              onChange={(e) =>
                setNewUser({ ...newUser, dob: e.target.value })
              }
            />
            </div>
            <div className="modal-detail">
            <label>Gender:</label>
            <input
              type="text"
              value={newUser.gender}
              onChange={(e) =>
                setNewUser({ ...newUser, gender: e.target.value })
              }
            />
            </div>
            <div className="modal-detail">
            <label>Food:</label>
            <input
              type="text"
              value={newUser.food}
              onChange={(e) =>
                setNewUser({ ...newUser, food: e.target.value })
              }
            />
            </div>
            <div className="modal-detail">
            <label>Hobbies:</label>
            <input
              type="text"
              value={newUser.hobbies}
              onChange={(e) =>
                setNewUser({ ...newUser, hobbies: e.target.value })
              }
            />
            </div>
            <button onClick={handleSaveUser}>Save</button>
          </div>
        </div>
      )}

      <div className="pagination">
        {usersList.length > usersPerPage && (
          <>
            {currentPage !== 1 && (
              <button
                className="prev-button"
                onClick={() => setCurrentPage(currentPage - 1)}
              >
              <Icon icon="ic:outline-arrow-back" />
              </button>
            )}
            <ul className="page-numbers">{renderPageNumbers}</ul>
            {currentPage !== pageNumbers.length && (
              <button
                className="next-button"
                onClick={() => setCurrentPage(currentPage + 1)}
              >   <Icon icon="material-symbols:arrow-forward"   />
              </button>
            )}
          </>
        )}
      </div>

      {selectedUser !== null && (
        <div className="user-view">
          <div className="user-card">
            <h2>{usersList[selectedUser].name}</h2>
            <div className="body">
              <div className="ques">AGE:</div>
              <div className="ans">{usersList[selectedUser].age}</div>
              </div>
              <div className="body">
              <div className="ques">DOB:</div>
              <div className="ans">{usersList[selectedUser].dob}</div>
              </div>
              <div className="body">
              <div className="ques">GENDER:</div>
                 <div className="ans">{usersList[selectedUser].gender}</div>
                 </div>
                  <div className="body">
                  <div className="ques">FOOD:</div>
                  <div className="ans">{usersList[selectedUser].food}</div>
                  </div>
                  <div className="body">
                  <div className="ques">HOBBIES:</div>
                  <div className="ans">{usersList[selectedUser].hobbies}</div>
                  </div>
              

          </div>
        </div>
      )}
    </>
  );
};

export default Users;
